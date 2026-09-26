from pr_reviewer.diff import annotate

PATCH = """@@ -10,5 +10,6 @@ def total(items):
     result = 0
-    for item in items:
+    for item in items[:5]:
         result += item.price
+    log(result)
     return result
\\ No newline at end of file
@@ -40,2 +41,3 @@ def other():
     a = 1
+    b = 2
"""


def test_new_side_lines_are_numbered_and_removed_lines_are_not() -> None:
    out = annotate(PATCH, 10_000)
    assert out.lines == {10, 11, 12, 13, 14, 41, 42}
    assert "   11 +    for item in items[:5]:" in out.text
    assert "      -    for item in items:" in out.text
    assert "\\ No newline" not in out.text
    assert not out.truncated


def test_truncation_stops_at_a_line_boundary_and_forgets_cut_lines() -> None:
    out = annotate(PATCH, 120)
    assert out.truncated
    assert out.text.endswith("\n".join(out.text.splitlines()[-1:]))
    assert max(out.lines) < 41
    assert all(f"{n:>5} " in out.text for n in out.lines)


def test_text_before_the_first_hunk_is_ignored() -> None:
    assert annotate("+not a hunk\n", 1000).lines == frozenset()
