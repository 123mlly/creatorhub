from __future__ import annotations

import unittest

from app.platforms.tiktok.browser_detail import (
    drama_episodes_from_payload,
    hit_from_payload, is_tt_noise_feed_url, is_tt_profile_feed_url,
    items_from_hrefs, items_from_payload, looks_like_video_bytes,
    pick_tt_play_url, play_url_candidates, user_detail_from_payload,
    user_meta_from_payload,
)
from app.platforms.tiktok.extract import (
    parse_tt_play_hit, parse_tt_web_item, tt_media_is_direct, tt_watch_candidates,
)
from app.platforms.tiktok.resolve import (
    canonicalize_tiktok_share_url,
    handle_from_user_ref,
    is_tiktok_shortdrama,
    resolve_tiktok_video_id,
    shortdrama_episode_url,
    shortdrama_parts,
)


class TikTokShareTests(unittest.TestCase):
    def test_shortdrama_id_and_flag(self):
        url = "https://www.tiktok.com/shortdrama/episode/7555488618694905110/1"
        self.assertTrue(is_tiktok_shortdrama(url))
        self.assertEqual(resolve_tiktok_video_id(url), "7555488618694905110")
        self.assertEqual(
            canonicalize_tiktok_share_url(url),
            "https://www.tiktok.com/@tiktok/video/7555488618694905110",
        )

    def test_shortdrama_parts_keep_episode_number(self):
        url = "https://www.tiktok.com/shortdrama/episode/7684885977403479057/1"
        self.assertEqual(shortdrama_parts(url), ("7684885977403479057", 1))
        self.assertEqual(
            shortdrama_episode_url("7684885977403479057", 12),
            "https://www.tiktok.com/shortdrama/episode/7684885977403479057/12",
        )

    def test_drama_series_keeps_only_matching_episodes(self):
        series = "7677865695564436501"
        parsed = drama_episodes_from_payload({
            "itemList": [
                {
                    "id": "7677867720364264718",
                    "desc": "Mighty Tyrant",
                    "author": {"uniqueId": "mialopez2249", "nickname": "Prime"},
                    "video": {"duration": 77, "playAddr": {
                        "UrlList": ["https://v16-webapp-prime.tiktok.com/video/tos/ep1.mp4"],
                    }},
                    "shortDramaSeriesInfo": {
                        "seriesId": series,
                        "seriesTitle": "Mighty Tyrant, Tender Love",
                        "episodeNumber": 1,
                        "episodeCount": 3,
                    },
                },
                {
                    "id": "7677867720364264999",
                    "desc": "Mighty Tyrant 2",
                    "video": {"duration": 80},
                    "shortDramaSeriesInfo": {
                        "seriesId": series,
                        "episodeNumber": 2,
                        "episodeCount": 3,
                    },
                },
                {
                    "id": "9999999999999999991",
                    "desc": "other",
                    "video": {"playAddr": {
                        "UrlList": ["https://v16-webapp-prime.tiktok.com/video/tos/other.mp4"],
                    }},
                    "shortDramaSeriesInfo": {
                        "seriesId": "1111111111111111111",
                        "seriesTitle": "Other",
                        "episodeNumber": 1,
                        "episodeCount": 10,
                    },
                },
            ]
        }, series)
        self.assertEqual(parsed["episode_count"], 3)
        self.assertEqual(parsed["title"], "Mighty Tyrant, Tender Love")
        self.assertEqual([ep["episode"] for ep in parsed["episodes"]], [1, 2])
        self.assertIn("/video/tos/ep1.mp4", parsed["episodes"][0]["play_url"])
        self.assertEqual(parsed["episodes"][0]["id"], "7677867720364264718")
        self.assertFalse(parsed["episodes"][1].get("play_url"))

    def test_drama_card_count_without_fake_episodes(self):
        series = "7684885977403479057"
        parsed = drama_episodes_from_payload({
            "dramaList": [{
                "dramaID": series,
                "dramaName": "The Dragon King",
                "numVideos": 65,
            }],
            "user": {"videoCount": 1700},
        }, series)
        self.assertEqual(parsed["episodes"], [])
        self.assertEqual(parsed["episode_count"], 65)
        self.assertEqual(parsed["title"], "The Dragon King")

    def test_regular_video_is_not_shortdrama(self):
        url = "https://www.tiktok.com/@foo/video/1234567890123456789"
        self.assertFalse(is_tiktok_shortdrama(url))
        self.assertEqual(resolve_tiktok_video_id(url), "1234567890123456789")

    def test_pick_prefers_mp4_over_image(self):
        play = pick_tt_play_url([
            "https://p16-sign.tiktokcdn.com/tos-alisg-p-0037/cover.jpeg",
            "https://v16-webapp-prime.tiktok.com/video/tos/foo/bar.mp4?expire=1",
        ])
        self.assertIn("/video/tos/", play)
        self.assertIn(".mp4", play)

    def test_pick_ignores_generic_cdn_api(self):
        self.assertEqual(pick_tt_play_url([
            "https://v16-webapp-prime.tiktok.com/api/recommend/item_list/",
            "https://v16.tiktokcdn.com/obj/config.bin",
        ]), "")

    def test_looks_like_video_bytes(self):
        self.assertTrue(looks_like_video_bytes(
            b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20))
        self.assertFalse(looks_like_video_bytes(
            b"\xa5\xef\x03\x00\x00\x00\x00m\x070.2.0.0\x07unknown" + b"\x00" * 20))

    def test_hit_from_item_struct(self):
        payload = {
            "__DEFAULT_SCOPE__": {
                "webapp.video-detail": {
                    "itemInfo": {
                        "itemStruct": {
                            "id": "7555488618694905110",
                            "desc": "短剧第一集",
                            "author": {"uniqueId": "drama", "nickname": "Drama"},
                            "video": {
                                "duration": 15,
                                "playAddr": {
                                    "UrlList": [
                                        "https://v16-webapp-prime.tiktok.com/video/tos/a.mp4",
                                    ]
                                },
                                "cover": {
                                    "UrlList": ["https://p16-sign.tiktokcdn.com/cover.jpeg"],
                                },
                            },
                            "stats": {"diggCount": 9, "commentCount": 2},
                        }
                    }
                }
            }
        }
        hit = hit_from_payload(payload)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], "7555488618694905110")
        self.assertTrue(hit["play_url"].endswith("a.mp4"))
        aweme = parse_tt_play_hit(hit, quality="highest")
        self.assertIsNotNone(aweme)
        self.assertEqual(aweme.platform, "tiktok")
        self.assertEqual(aweme.aweme_id, "7555488618694905110")
        self.assertEqual(aweme.medias[0].url, hit["play_url"])
        self.assertEqual(aweme.quality_label, "highest")

    def test_items_from_profile_feed(self):
        payload = {
            "itemList": [{
                "id": "1111111111111111111",
                "desc": "hello",
                "author": {"uniqueId": "foo", "nickname": "Foo"},
                "video": {
                    "duration": 8,
                    "playAddr": {
                        "UrlList": ["https://v16-webapp-prime.tiktok.com/video/tos/a.mp4"],
                    },
                    "cover": {"UrlList": ["https://p16-sign.tiktokcdn.com/cover.jpeg"]},
                },
                "stats": {"diggCount": 3, "commentCount": 1},
            }],
            "userInfo": {"user": {"uniqueId": "foo", "nickname": "Foo", "id": "99"}},
        }
        items = items_from_payload(payload)
        self.assertEqual(len(items), 1)
        aweme = parse_tt_web_item(items[0], quality="highest")
        self.assertEqual(aweme.aweme_id, "1111111111111111111")
        self.assertTrue(tt_media_is_direct(aweme.medias[0].url))
        meta = user_meta_from_payload(payload)
        self.assertEqual(meta.get("douyin_id"), "@foo")
        self.assertFalse(tt_media_is_direct(
            "https://www.tiktok.com/@foo/video/1111111111111111111"))

    def test_profile_meta_ignores_logged_in_user(self):
        url = "https://www.tiktok.com/@artisan.ai_drama_can"
        self.assertEqual(handle_from_user_ref(url), "artisan.ai_drama_can")
        payload = {
            "__DEFAULT_SCOPE__": {
                "webapp.app-context": {
                    "user": {"uniqueId": "my_login", "nickname": "我自己"},
                },
                "webapp.user-detail": {
                    "userInfo": {
                        "user": {
                            "uniqueId": "artisan.ai_drama_can",
                            "nickname": "Artisan Drama",
                            "id": "755",
                        }
                    }
                },
            }
        }
        meta = user_meta_from_payload(payload, want_handle="artisan.ai_drama_can")
        self.assertEqual(meta.get("douyin_id"), "@artisan.ai_drama_can")
        self.assertEqual(meta.get("nickname"), "Artisan Drama")
        stray = items_from_payload({
            "itemList": [{
                "id": "2222222222222222222",
                "desc": "not them",
                "author": {"uniqueId": "my_login", "nickname": "我自己"},
                "video": {"playAddr": {"UrlList": [
                    "https://v16-webapp-prime.tiktok.com/video/tos/x.mp4"]}},
            }]
        })
        self.assertEqual(stray[0]["_hit"]["handle"], "my_login")

    def test_shortdrama_item_without_video_dict(self):
        items = items_from_payload({
            "itemList": [{
                "id": "7555488618694905110",
                "desc": "ep1",
                "author": {"uniqueId": "artisan.ai_drama_can", "nickname": "AI Drama Lab"},
                "shortDramaSeriesInfo": {"title": "Drama", "episodeNo": 1},
            }]
        })
        self.assertEqual(len(items), 1)
        aweme = parse_tt_web_item(items[0], quality="highest")
        self.assertEqual(aweme.aweme_id, "7555488618694905110")
        self.assertIn("/shortdrama/episode/", aweme.medias[0].url)

    def test_profile_hrefs_collect_drama_and_video(self):
        items = items_from_hrefs([
            "https://www.tiktok.com/shortdrama/episode/7555488618694905110/1",
            "https://www.tiktok.com/@foo/video/1234567890123456789",
            "https://www.tiktok.com/@tiktok/video/1596260977708038",
        ], handle="artisan.ai_drama_can")
        by_id = {it["_hit"]["id"]: it["_hit"] for it in items}
        self.assertEqual(by_id["7555488618694905110"]["kind"], "shortdrama")
        self.assertEqual(by_id["7555488618694905110"]["handle"], "artisan.ai_drama_can")
        self.assertEqual(by_id["1234567890123456789"]["handle"], "foo")
        self.assertEqual(by_id["1596260977708038"]["handle"], "tiktok")

    def test_watch_candidates_include_drama_and_video(self):
        aweme = parse_tt_web_item({
            "_hit": {
                "id": "7636693955682157589",
                "kind": "shortdrama",
                "handle": "artisan.ai_drama_can",
                "author": "AI Drama Lab",
                "title": "ep",
            }
        })
        cands = tt_watch_candidates(aweme)
        self.assertTrue(any("/shortdrama/episode/7636693955682157589" in u for u in cands))
        self.assertTrue(any("/@artisan.ai_drama_can/video/7636693955682157589" in u for u in cands))
        self.assertFalse(any("/@tiktok/video/" in u for u in cands))

    def test_shop_video_does_not_open_shortdrama(self):
        aweme = parse_tt_web_item({
            "_hit": {
                "id": "1111111111111111111",
                "kind": "video",
                "handle": "sawasdeeshop2",
                "author": "Sawasdee Shop",
                "title": "product",
            }
        })
        cands = tt_watch_candidates(aweme)
        self.assertTrue(any("/@sawasdeeshop2/video/1111111111111111111" in u for u in cands))
        self.assertFalse(any("/shortdrama/" in u for u in cands))

    def test_watch_candidates_skip_default_tiktok_handle(self):
        aweme = parse_tt_web_item({
            "_hit": {
                "id": "7636693955682157589",
                "kind": "video",
                "handle": "",
                "author": "AI Drama Lab",
                "title": "ep",
            }
        })
        cands = tt_watch_candidates(aweme)
        self.assertTrue(any("/shortdrama/episode/7636693955682157589" in u for u in cands))
        self.assertFalse(any("/@tiktok/video/" in u for u in cands))

    def test_noise_feed_url(self):
        self.assertTrue(is_tt_noise_feed_url(
            "https://www.tiktok.com/api/recommend/item_list/?aid=1988"))
        self.assertFalse(is_tt_noise_feed_url(
            "https://www.tiktok.com/api/post/item_list/?secUid=MS4w"))
        self.assertTrue(is_tt_profile_feed_url(
            "https://www.tiktok.com/api/drama/user/drama_list/?secUid=MS4w"))

    def test_profile_keeps_shop_posts_even_if_is_ad(self):
        items = items_from_payload({
            "itemList": [
                {
                    "id": "1111111111111111111",
                    "desc": "shop",
                    "isAd": True,
                    "author": {"uniqueId": "sawasdeeshop2"},
                    "video": {"playAddr": {"UrlList": [
                        "https://v16-webapp-prime.tiktok.com/video/tos/shop.mp4"]}},
                },
            ]
        })
        self.assertEqual(items[0]["_hit"]["id"], "1111111111111111111")
        ad_hit = hit_from_payload({
            "itemStruct": {
                "id": "1111111111111111111",
                "isAd": True,
                "author": {"uniqueId": "other"},
                "video": {"playAddr": {"UrlList": [
                    "https://v16-webapp-prime.tiktok.com/video/tos/ad.mp4"]}},
            }
        }, want_id="9999999999999999999")
        self.assertIsNone(ad_hit)
        shop_hit = hit_from_payload({
            "itemStruct": {
                "id": "1111111111111111111",
                "isAd": True,
                "author": {"uniqueId": "sawasdeeshop2"},
                "video": {"playAddr": {"UrlList": [
                    "https://v16-webapp-prime.tiktok.com/video/tos/shop.mp4"]}},
            }
        }, want_id="1111111111111111111")
        self.assertIsNotNone(shop_hit)
        self.assertIn("/video/tos/shop.mp4", shop_hit["play_url"])
        stray = items_from_payload({
            "statusCode": 0,
            "itemList": [],
            "extra": {
                "itemStruct": {
                    "id": "7368706984613233670",
                    "isAd": True,
                    "author": {"uniqueId": "other"},
                    "video": {"playAddr": {"UrlList": [
                        "https://v16-webapp-prime.tiktok.com/video/tos/ad.mp4"]}},
                }
            },
        }, list_keys_only=True)
        self.assertEqual(stray, [])
        own = items_from_payload({
            "itemList": [{
                "id": "1111111111111111111",
                "isAd": True,
                "author": {"uniqueId": "sawasdeeshop2"},
                "video": {"playAddr": {"UrlList": [
                    "https://v16-webapp-prime.tiktok.com/video/tos/shop.mp4"]}},
            }]
        }, list_keys_only=True)
        self.assertEqual(own[0]["_hit"]["id"], "1111111111111111111")

    def test_drama_list_items(self):
        items = items_from_payload({
            "dramaList": [{
                "dramaID": "7674268460205364245",
                "dramaName": "El regreso del rey dragón",
                "description": "Leo Lara sale de prisión",
                "numVideos": 65,
                "cover": {"urlList": [
                    "https://p16-sign.tiktokcdn.com/tos-alisg/cover.jpeg",
                ]},
            }]
        })
        self.assertEqual(len(items), 1)
        hit = items[0]["_hit"]
        self.assertEqual(hit["id"], "7674268460205364245")
        self.assertEqual(hit["kind"], "shortdrama")
        self.assertEqual(hit["title"], "El regreso del rey dragón")
        self.assertTrue(hit["cover"].startswith("http"))
        aweme = parse_tt_web_item(items[0], quality="highest")
        self.assertIn("/shortdrama/episode/7674268460205364245", aweme.medias[0].url)

    def test_user_detail_scope_drops_fyp_module(self):
        payload = {
            "__DEFAULT_SCOPE__": {
                "webapp.user-detail": {
                    "itemList": [{
                        "id": "1111111111111111111",
                        "desc": "mine",
                        "author": {"uniqueId": "artisan.ai_drama_can"},
                        "video": {"playAddr": {"UrlList": [
                            "https://v16-webapp-prime.tiktok.com/video/tos/a.mp4"]}},
                    }],
                    "userInfo": {"user": {
                        "uniqueId": "artisan.ai_drama_can", "nickname": "AI Drama Lab",
                    }},
                },
            },
            "ItemModule": {
                "1596260977708038": {
                    "id": "1596260977708038",
                    "desc": "Whether you're baking a cake",
                    "author": {"uniqueId": "tiktok"},
                    "video": {"playAddr": {"UrlList": [
                        "https://v16-webapp-prime.tiktok.com/video/tos/b.mp4"]}},
                }
            },
        }
        scoped = user_detail_from_payload(payload)
        ids = {it["_hit"]["id"] for it in items_from_payload(scoped)}
        self.assertEqual(ids, {"1111111111111111111"})
        mixed = {it["_hit"]["id"] for it in items_from_payload(payload)}
        self.assertIn("1596260977708038", mixed)

    def test_h264_bitrate_preferred_over_hevc(self):
        hit = hit_from_payload({
            "itemStruct": {
                "id": "1111111111111111111",
                "author": {"uniqueId": "foo"},
                "video": {
                    "playAddr": {
                        "UrlList": ["https://v16-webapp-prime.tiktok.com/video/tos/hevc.mp4"],
                    },
                    "bitrateInfo": [
                        {
                            "CodecType": "h265",
                            "PlayAddr": {"UrlList": [
                                "https://v16-webapp-prime.tiktok.com/video/tos/hevc.mp4"]},
                        },
                        {
                            "CodecType": "h264",
                            "PlayAddr": {"UrlList": [
                                "https://v16-webapp-prime.tiktok.com/video/tos/avc.mp4"]},
                        },
                    ],
                },
            }
        })
        self.assertIn("/video/tos/avc.mp4", hit["play_url"])
        self.assertEqual(play_url_candidates(hit)[0], hit["play_url"])

    def test_play_addr_preferred_over_watermarked_download(self):
        hit = hit_from_payload({
            "itemStruct": {
                "id": "1111111111111111111",
                "author": {"uniqueId": "foo"},
                "video": {
                    "downloadAddr": {
                        "UrlList": [
                            "https://v16-webapp-prime.tiktok.com/video/tos/wm.mp4?watermark=1",
                        ]
                    },
                    "playAddr": {
                        "UrlList": [
                            "https://v16-webapp-prime.tiktok.com/video/tos/clean.mp4?lr=unwatermarked",
                        ]
                    },
                },
            }
        })
        self.assertIn("/video/tos/clean.mp4", hit["play_url"])
        self.assertNotIn("watermark=1", hit["play_url"])


if __name__ == "__main__":
    unittest.main()
