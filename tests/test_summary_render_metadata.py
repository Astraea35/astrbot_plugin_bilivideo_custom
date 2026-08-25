"""Video tags, comments, and text-statistics presentation tests."""

from __future__ import annotations

import pytest

from bilivideo.api.endpoints import get_featured_comments
from bilivideo.core.types import FeaturedComment, FeaturedCommentReply, VideoInfo
from bilivideo.handlers._render_helper import _build_summary_render_markdown
from bilivideo.messaging.builders import format_video_stats


def _video() -> VideoInfo:
    return VideoInfo(
        bvid="BV1M8gN6sEUr",
        title="测试视频",
        category="知识",
        aid=123,
        duration=516,
        view=3593,
        danmaku=74,
        like=258,
        coin=61,
        favorite=93,
        reply=17,
        share=8,
    )


def test_plain_video_info_contains_all_seven_interaction_counters() -> None:
    stats = format_video_stats(_video())

    for label in ("播放", "弹幕", "点赞", "投币", "收藏", "评论", "分享"):
        assert label in stats


def test_summary_image_markup_has_tags_and_comments_but_no_stats_row() -> None:
    comments = (
        FeaturedComment(
            author_name="小明同学",
            content="终于把字幕和语音转写讲明白了。",
            like=12000,
            replies=(FeaturedCommentReply(author_name="作者", content="有可用文本就不下载音频。"),),
        ),
    )

    rendered = _build_summary_render_markdown("# 测试视频\n\n## 要点\n内容", _video(), comments)

    assert 'class="video-tags"' in rendered
    assert "知识" in rendered
    assert "时长 08:36" in rendered
    assert "BV1M8gN6sEUr" in rendered
    assert "前排评论" in rendered
    assert "小明同学" in rendered
    assert "作者" in rendered
    assert "播放" not in rendered
    assert "投币" not in rendered


class _CommentClient:
    async def request_json(self, _method, _url, *, params=None):
        assert params == {"oid": 123, "type": 1, "pn": 1, "sort": 2}
        return {
            "data": {
                "replies": [
                    {
                        "member": {"uname": "评论者"},
                        "content": {"message": "主评论"},
                        "like": 42,
                        "replies": [
                            {
                                "member": {"uname": "回复者"},
                                "content": {"message": "回复内容"},
                                "like": 7,
                            }
                        ],
                    }
                ]
            }
        }


@pytest.mark.asyncio
async def test_featured_comments_extracts_comment_and_reply() -> None:
    comments = await get_featured_comments(_CommentClient(), _video(), count=2, reply_count=1)

    assert len(comments) == 1
    assert comments[0].author_name == "评论者"
    assert comments[0].content == "主评论"
    assert comments[0].replies[0].author_name == "回复者"


@pytest.mark.asyncio
async def test_featured_comments_preserves_emotes_and_pictures_as_html() -> None:
    class _RichCommentClient:
        async def request_json(self, _method, _url, *, params=None):
            return {
                "data": {
                    "replies": [
                        {
                            "member": {"uname": "图文评论者"},
                            "content": {
                                "message": "好耶[doge]",
                                "emote": {"[doge]": {"url": "http://i0.hdslb.com/emote.png"}},
                                "pictures": [{"img_src": "//i0.hdslb.com/comment.jpg"}],
                            },
                            "replies": [],
                        }
                    ]
                }
            }

    comments = await get_featured_comments(_RichCommentClient(), _video(), count=1, reply_count=0)

    assert '<img class="comment-emote"' in comments[0].content_html
    assert 'src="https://i0.hdslb.com/emote.png"' in comments[0].content_html
    assert '<img class="comment-picture"' in comments[0].content_html
    assert "[doge]" not in comments[0].content_html
