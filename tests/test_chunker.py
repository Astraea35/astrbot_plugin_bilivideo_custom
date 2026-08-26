"""Chunker unit tests."""

from __future__ import annotations

from bilivideo.messaging.chunker import format_count, split_text_for_messages


class TestSplitTextForMessages:
    def test_short_unchanged(self) -> None:
        assert split_text_for_messages("hello", max_chunk=100) == ["hello"]

    def test_breaks_on_paragraph(self) -> None:
        text = "A" * 60 + "\n\n" + "B" * 60
        chunks = split_text_for_messages(text, max_chunk=70)
        assert len(chunks) == 2
        assert chunks[0].count("A") == 60
        assert chunks[1].count("B") == 60

    def test_falls_back_to_hard_cut(self) -> None:
        text = "X" * 100
        chunks = split_text_for_messages(text, max_chunk=40)
        assert len(chunks) >= 2
        assert sum(len(c) for c in chunks) == 100

    def test_no_newline_chunks_within_limit(self) -> None:
        text = "Y" * 250
        chunks = split_text_for_messages(text, max_chunk=40)
        assert all(len(c) <= 40 for c in chunks)
        assert "".join(chunks) == text

    def test_newline_near_start_produces_valid_chunks(self) -> None:
        text = "A\n" + "B" * 200
        chunks = split_text_for_messages(text, max_chunk=40)
        assert all(len(c) <= 40 for c in chunks)
        assert all(c for c in chunks)
        assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


class TestFormatCount:
    def test_small(self) -> None:
        assert format_count(123) == "123"

    def test_wan(self) -> None:
        assert format_count(15000) == "1.5万"

    def test_yi(self) -> None:
        assert format_count(150_000_000) == "1.5亿"


def test_format_video_summary_lines_description_no_truncate_by_default() -> None:
    from bilivideo.core.config import PluginConfig
    from bilivideo.core.types import VideoInfo
    from bilivideo.messaging.builders import format_video_summary_lines, format_video_info_block

    long_desc = "剧场版教程 录制剪辑就用了1周 希望这样的长视频还有人能看 播放别太惨淡吧~\n记得三连投币啊！\n★ 材质包：梧桐加减法 \n★ 本期存档+投影链接：\n百度网盘：https://pan.baidu.com/s/1234567890abcdefg 提取码: 1234"
    info = VideoInfo(
        bvid="BV1vp4y1K7tS",
        title="【大型建筑教程】海上方舟",
        pic="https://example.com/cover.jpg",
        desc=long_desc,
        pubdate=1692675540,
        duration=3600,
        category="Minecraft",
        aid=123456,
        owner_name="咸到老时变成鱼",
        owner_mid="12345",
        view=478000,
        danmaku=1527,
        like=16000,
        coin=6962,
        favorite=30000,
        reply=850,
        share=10000,
    )

    # 1. Default config (detect_desc_max_len=0) should NOT truncate description
    cfg = PluginConfig.from_mapping({})
    lines = format_video_summary_lines(info, config=cfg)
    desc_line = next(line for line in lines if line.startswith("📝 简介: "))
    assert f"📝 简介: {long_desc}" == desc_line
    assert "https://pan.baidu.com/s/1234567890abcdefg" in desc_line

    # 2. Config with detect_desc_max_len > 0 should truncate
    cfg_limit = PluginConfig.from_mapping({"detect_desc_max_len": 50})
    lines_limit = format_video_summary_lines(info, config=cfg_limit)
    desc_line_limit = next(line for line in lines_limit if line.startswith("📝 简介: "))
    assert desc_line_limit == f"📝 简介: {long_desc[:50]}..."

    # 3. format_video_info_block default should not truncate
    block = format_video_info_block(info)
    assert long_desc in block

