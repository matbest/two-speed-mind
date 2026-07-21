"""Slice 8c — user profiles (spec §34). Each profile is its own mind on disk."""
from kaineros.cli import Session
from kaineros import profiles


def test_switching_profiles_swaps_the_whole_mind(tmp_path):
    alice_dir = str(tmp_path / "alice" / "mind")
    bob_dir = str(tmp_path / "bob" / "mind")

    s = Session(store_dir=alice_dir)
    s.turn("bananas are yellow")
    s.turn("moon orbits earth")
    s.turn("tea is hot")  # promotes bananas into alice's wiki
    assert s.store.page("bananas") is not None

    s.load_profile(bob_dir)  # a different person, a fresh mind
    assert s.store.pages() == []
    assert s.buffer == []                       # new conversation
    s.turn("cats say meow")
    s.turn("dogs say woof")
    s.turn("owls say hoot")
    assert s.store.page("cats") is not None
    assert s.store.page("bananas") is None      # bob doesn't know alice's facts

    s.load_profile(alice_dir)                   # back to alice — her mind persisted
    assert s.store.page("bananas") is not None
    assert s.store.page("cats") is None


def test_list_profiles_reads_the_profiles_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KAINEROS_HOME", str(tmp_path))
    assert profiles.list_profiles() == []
    Session(store_dir=profiles.mind_dir("programmer")).turn("i use rust daily")
    Session(store_dir=profiles.mind_dir("teenager")).turn("i love tiktok")
    assert profiles.list_profiles() == ["programmer", "teenager"]


def test_no_name_is_the_default_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("KAINEROS_HOME", str(tmp_path))
    d = profiles.default_mind_dir()
    assert d.endswith(str(__import__("os").path.join("profiles", "default", "mind")))


def test_legacy_mind_migrates_into_default_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("KAINEROS_HOME", str(tmp_path))
    legacy = str(tmp_path / "mind")  # a pre-profiles mind
    Session(store_dir=legacy).turn("i live in st leonards")  # a real fact on disk
    assert (tmp_path / "mind" / "pool.json").exists()

    dest = profiles.default_mind_dir()  # first launch under the profiles model
    assert (tmp_path / "profiles" / "default" / "mind" / "pool.json").exists()
    assert not (tmp_path / "mind").exists()          # legacy moved, not copied
    assert Session(store_dir=dest).store.pages()       # the fact survived the migration
    assert "default" in profiles.list_profiles()
