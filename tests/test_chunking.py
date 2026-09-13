from app.chunking import MAX_CHARS, MIN_CHARS, chunk_judgment, clean


def para(n: int, length: int) -> str:
    return f"[{n}] " + ("word " * (length // 5))[: length - 5]


def test_numbered_paragraphs_keep_anchors():
    text = "Parties block\n" + "\n".join(para(n, 600) for n in range(1, 6))
    chunks = chunk_judgment(text)

    assert chunks[0].para_no is None and "Parties block" in chunks[0].text
    assert [(c.para_no, c.para_end) for c in chunks[1:]] == [(n, n) for n in range(1, 6)]


def test_short_paragraphs_merge_until_min_size():
    text = "\n".join(para(n, 150) for n in range(1, 10))
    chunks = chunk_judgment(text)

    assert chunks[0].para_no == 1 and chunks[0].para_end == 3
    assert all(len(c.text) >= MIN_CHARS for c in chunks[:-1])
    covered = [n for c in chunks for n in range(c.para_no, c.para_end + 1)]
    assert covered == list(range(1, 10))


def test_long_paragraph_splits_into_windows_with_same_anchor():
    text = "\n".join([para(1, 500), para(2, 4000), para(3, 500)])
    chunks = chunk_judgment(text)

    para2 = [c for c in chunks if c.para_no == 2]
    assert len(para2) >= 3
    assert all(len(c.text) <= MAX_CHARS for c in chunks)


def test_out_of_sequence_markers_stay_inside_paragraph():
    quoted = "The court below said: [15] something quoted from another decision."
    text = "\n".join([para(1, 500), para(2, 500) + " " + quoted, para(3, 500)])
    chunks = chunk_judgment(text)

    assert [c.para_no for c in chunks] == [1, 2, 3]
    assert "[15] something quoted" in chunks[1].text


def test_inline_markers_after_headings_are_detected():
    text = " ".join(f"Heading {n} [{n}] " + "text " * 100 for n in range(1, 5))
    chunks = chunk_judgment(text)

    assert [c.para_no for c in chunks if c.para_no] == [1, 2, 3, 4]


def test_footnote_style_markers_without_space_are_ignored():
    text = "\n".join(para(n, 500) for n in range(1, 4)) + " the Act[4] applies."
    assert [c.para_no for c in chunk_judgment(text)] == [1, 2, 3]


def test_unnumbered_text_falls_back_to_overlapping_windows():
    text = "\n".join("Sentence number %d of an old judgment." % i for i in range(300))
    chunks = chunk_judgment(text)

    assert len(chunks) > 1
    assert all(c.para_no is None for c in chunks)
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    first_tail = chunks[0].text[-60:]
    assert first_tail.split("\n")[-1][:20] in chunks[1].text


def test_clean_normalizes_whitespace_and_strips_nul():
    assert clean("a\x00b \t c\r\n\r\n  d  ") == "ab c\nd"


def test_empty_text_yields_no_chunks():
    assert chunk_judgment("") == []
