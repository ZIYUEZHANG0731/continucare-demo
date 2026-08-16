from pathlib import Path


def test_successful_final_review_edit_returns_to_submit_mode():
    source = (
        Path(__file__).parents[1] / "patient-web" / "src" / "main.jsx"
    ).read_text(encoding="utf-8")

    final_review = source[source.index("function FinalReview"):source.index("function Completed")]
    assert "if (succeeded)" in final_review
    assert 'setSelectedLink("")' in final_review
    assert "setAddingAdditional(false)" in final_review
    assert "返回完整复核" in final_review
    assert "确认并提交今天记录" in final_review
