import unittest

from app.core.semantic_selection import (
    SemanticSelectionError,
    complete_hook_opening_before_promo,
    infer_sku_id,
    make_full_hook_unit,
    make_units,
    normalize_sku_id,
    normalize_topic_id,
    plan_semantic_mix,
    unit_rejection_reason,
)


def unit(source, role, text, *, topic="usage", sku="JXB-99", start=0.0,
         duration=2.0, hook=False, body=True, **overrides):
    value = {
        "source_id": source,
        "path": f"{source}.mp4",
        "file": f"{source}.mp4",
        "in_s": start,
        "out_s": start + duration,
        "start": start,
        "duration_s": duration,
        "duration": duration,
        "has_audio": True,
        "text": text,
        "role": role,
        "sku_id": sku,
        "topic_id": topic,
        "sentence_complete": True,
        "visual_offer_status": "none",
        "offer_status": "none",
        "is_hook_candidate": hook,
        "is_body_candidate": body,
        "introduces": (),
        "requires": (),
    }
    value.update(overrides)
    return value


class SemanticSelectionTests(unittest.TestCase):
    def test_hook_prefix_ends_on_complete_thought_before_old_offer(self):
        cues = [
            {"start": 0.0, "end": 1.6, "text": "俊小白色修牙膏要用完了"},
            {"start": 1.6, "end": 2.2, "text": "你买了吗"},
            {"start": 2.2, "end": 3.4, "text": "不然我嘴里又有味了"},
            {"start": 3.4, "end": 4.6, "text": "买了跟之前一样"},
            {"start": 4.6, "end": 5.6, "text": "39块9"},
            {"start": 5.6, "end": 7.0, "text": "国庆拍一发六"},
        ]
        opening = complete_hook_opening_before_promo(cues, full_duration_s=7.0)
        self.assertIsNotNone(opening)
        selected, end_s = opening
        self.assertEqual(end_s, 3.4)
        self.assertEqual(len(selected), 3)
        self.assertNotIn("39块", "".join(item["text"] for item in selected))
        opening_units = make_units(selected, source_id="opening", path="色修.mp4",
                                   is_hook_candidate=True)
        whole_thought = make_full_hook_unit(opening_units, full_duration_s=end_s)
        self.assertTrue(whole_thought["sentence_complete"])
        self.assertEqual(whole_thought["out_s"], 3.4)

        price_question = [
            {"start": 0.0, "end": 1.8, "text": "你买的俊小白色修牙膏多少钱"},
            {"start": 1.8, "end": 2.7, "text": "39块9"},
        ]
        self.assertIsNone(complete_hook_opening_before_promo(
            price_question, full_duration_s=2.7))

    def test_body_oral_freshness_thought_stays_whole_before_promotion(self):
        cues = [
            {"start": 34.88, "end": 36.12, "text": "自然就不容易着色了"},
            {"start": 36.12, "end": 39.0, "text": "关键是它加入了香氛口气清新技术"},
            {"start": 39.0, "end": 40.08, "text": "刷完牙齿之后"},
            {"start": 40.08, "end": 41.6, "text": "打个嗝都是香香的"},
            {"start": 41.6, "end": 43.4, "text": "还在犹豫的朋友也不用担心了"},
            {"start": 43.4, "end": 45.0, "text": "现在下单三支送两支"},
        ]
        units = make_units(cues, source_id="body", path="色修牙膏.mp4",
                           is_body_candidate=True)
        selected = next(item for item in units if item["in_s"] == 36.12)
        self.assertEqual(selected["out_s"], 41.6)
        self.assertTrue(selected["sentence_complete"])
        self.assertEqual(selected["product_id"], "JXB-COLOR")
        self.assertNotIn("下单", selected["text"])

    def test_sku_requires_product_evidence_and_rejects_mixed_products(self):
        self.assertEqual(infer_sku_id("原片-修护-小如.mp4", "牙齿敏感吗"), "JXB-99")
        self.assertEqual(infer_sku_id("色修牙膏.mp4", ""), "JXB-COLOR")
        self.assertEqual(infer_sku_id("乳牙期.mp4", ""), "JXB-KIDS-PRIMARY")
        self.assertIsNone(infer_sku_id("unknown.mp4", "牙齿敏感吗"))
        self.assertIsNone(infer_sku_id("unknown.mp4", "普通美白牙膏"))
        self.assertIsNone(infer_sku_id("修护.mp4", "换牙期和99修护牙膏"))
        self.assertIsNone(infer_sku_id("修护.mp4", "红色蓝色白色三款"))
        self.assertEqual(normalize_sku_id("99"), "JXB-99")
        self.assertEqual(normalize_sku_id("JXB-99"), "JXB-99")
        self.assertEqual(normalize_sku_id("99牙膏"), "JXB-99")
        self.assertEqual(normalize_sku_id("修护牙膏"), "JXB-99")
        self.assertEqual(normalize_sku_id("色修牙膏"), "JXB-COLOR")
        self.assertEqual(normalize_sku_id("俊小白色修牙膏"), "JXB-COLOR")
        self.assertEqual(normalize_topic_id("牙齿敏感"), "sensitivity")
        self.assertIsNone(normalize_sku_id("儿童"))
        self.assertIsNone(normalize_topic_id("口气美白"))
        self.assertEqual(infer_sku_id("创意.mp4", "这支四球牙膏适合黄牙"), "JXB-COLOR")
        self.assertEqual(infer_sku_id("创意.mp4", "这支社羞牙膏怎么用"), "JXB-COLOR")
        self.assertIsNone(infer_sku_id("创意.mp4", "比赛要进四球"))
        self.assertIsNone(infer_sku_id("JXB-99.mp4", "这支四球牙膏适合黄牙"))

    def test_unpunctuated_spoken_punchline_is_complete_but_fragment_is_not(self):
        complete = make_units(
            [{"start": 0, "end": 2, "text": "那我抓你去抢"}],
            source_id="punchline", path="色修.mp4", is_hook_candidate=True,
        )
        self.assertTrue(complete[0]["sentence_complete"])
        self.assertEqual(complete[0]["boundary_basis"], "inferred")
        fragment = make_units(
            [{"start": 0, "end": 2, "text": "那我把"}],
            source_id="fragment", path="色修.mp4", is_hook_candidate=True,
        )
        self.assertFalse(fragment[0]["sentence_complete"])

    def test_verified_promotion_status_survives_whole_hook_only_when_all_cues_checked(self):
        cues = [
            {"start": 0.1, "end": 1.1, "text": "黄牙怎么办？", "offer_status": "current"},
            {"start": 1.2, "end": 2.5, "text": "国庆破价，39元。", "offer_status": "current"},
        ]
        verified = make_full_hook_unit(
            make_units(cues, source_id="verified", path="色修.mp4",
                       is_hook_candidate=True, default_visual_offer_status="none"),
            full_duration_s=3.0,
        )
        self.assertTrue(verified["sentence_complete"])
        self.assertEqual(verified["offer_status"], "current")
        unverified = make_full_hook_unit(
            make_units([dict(cues[0], offer_status="none"), cues[1]],
                       source_id="unverified", path="色修.mp4",
                       is_hook_candidate=True, default_visual_offer_status="none"),
            full_duration_s=3.0,
        )
        self.assertEqual(unverified["offer_status"], "unknown")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([unverified], sku_id="色修", topic_id="美白")
        self.assertIn("口播活动或价格未通过当期核对", caught.exception.details["rejections"])

    def test_conflicting_promotional_counts_cannot_join_even_when_marked_current(self):
        hook = unit("h", "hook", "黄牙吗？拍一发六送三支牙刷。", topic="whitening",
                    sku="JXB-COLOR", hook=True, body=False, offer_status="current")
        middle = unit("m", "demonstrate", "下单三支送两支牙刷，再来刷牙。",
                      topic="whitening", sku="JXB-COLOR", offer_status="current")
        product = unit("p", "product", "这是俊小白色修牙膏。", topic="",
                       sku="JXB-COLOR")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, middle, product], sku_id="色修",
                              topic_id="美白", target_clips=3)
        self.assertEqual(caught.exception.code, "no_coherent_path")

    def test_unpunctuated_asr_merges_dependent_cues_into_complete_speech(self):
        # Exact utterance shape from the local 104432998 transcript: ASR rows
        # have timestamps but no punctuation.  The later “就” cues belong to
        # the preceding amount instruction.
        cues = [
            {"start": 0.0, "end": 1.3, "text": "先别沾水啊", "role": "hook"},
            {"start": 1.3, "end": 2.3, "text": "不用挤太多", "role": "demonstrate"},
            {"start": 2.3, "end": 3.3, "text": "就这么一点点", "role": "demonstrate"},
            {"start": 3.3, "end": 4.3, "text": "就可以了啊", "role": "demonstrate"},
        ]
        units = make_units(
            cues, source_id="104432998", path="原片-修护-104432998.mp4",
            sku_id="JXB-99", topic_id="usage", is_hook_candidate=True,
            is_body_candidate=True, default_visual_offer_status="none",
        )
        self.assertEqual(len(units), 2)
        self.assertEqual([(x["in_s"], x["out_s"]) for x in units], [(0.0, 1.3), (1.3, 4.3)])
        self.assertEqual(units[1]["text"], "不用挤太多就这么一点点就可以了啊")
        self.assertTrue(all(x["sentence_complete"] for x in units))
        self.assertEqual(units[0]["topic_source"], "spoken")
        self.assertEqual(units[1]["topic_id"], "usage")

    def test_ui_topic_does_not_label_unrelated_speech(self):
        units = make_units(
            [{"start": 0, "end": 2, "text": "今天天气不错。", "role": "hook"}],
            source_id="a", path="修护.mp4", sku_id="JXB-99", topic_id="sensitivity",
            is_hook_candidate=True, default_visual_offer_status="none",
        )
        self.assertEqual(units[0]["topic_id"], "")
        self.assertEqual(units[0]["topic_source"], "unresolved")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix(units, sku_id="JXB-99", topic_id="sensitivity")
        self.assertEqual(caught.exception.code, "missing_hook")

        unknown_product = make_units(
            [{"start": 0, "end": 2, "text": "这是牙膏。", "role": "product"}],
            source_id="unknown", path="unlabelled.mp4", sku_id="JXB-99",
        )
        self.assertEqual(unknown_product[0]["sku_id"], "")
        self.assertEqual(unknown_product[0]["sku_evidence"], "unknown_or_mixed")

    def test_complete_source_ranges_are_ordered_with_variable_duration(self):
        hook = unit("hook", "hook", "先别沾水啊", duration=1.3, hook=True,
                    body=False, introduces=("problem:usage",))
        demonstration = unit("body", "demonstrate", "不用挤太多，就这么一点点就可以了啊。",
                             duration=3.4, requires=("problem:usage",))
        product = unit("body", "product", "就是俊小白的修护牙膏。", start=5.0,
                       duration=2.1, introduces=("sku:JXB-99",))
        plan = plan_semantic_mix([product, hook, demonstration], sku_id="JXB-99",
                                 topic_id="usage", target_clips=3)
        self.assertEqual([x["role"] for x in plan["segments"]],
                         ["hook", "demonstrate", "product"])
        self.assertEqual([x["duration"] for x in plan["segments"]], [1.3, 3.4, 2.1])
        self.assertAlmostEqual(plan["duration_s"], 6.8)
        self.assertEqual(plan["segments"][0]["file"], "hook.mp4")
        self.assertEqual(plan["transcript"], hook["text"] + demonstration["text"] + product["text"])

    def test_hook_and_body_membership_is_enforced(self):
        hook = unit("hook", "hook", "为什么牙齿敏感？", topic="sensitivity", hook=True, body=False)
        middle = unit("body", "explain", "因为日常护理方式不同。", topic="sensitivity")
        product = unit("body", "product", "这是俊小白修护牙膏。", topic="", start=2.5,
                       introduces=("sku:JXB-99",))
        plan = plan_semantic_mix([hook, middle, product], sku_id="JXB-99",
                                 topic_id="sensitivity", hook_files=["hook.mp4"],
                                 body_files=["body.mp4"], target_clips=3)
        self.assertEqual(len(plan["segments"]), 3)
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, middle, product], sku_id="JXB-99",
                              topic_id="sensitivity", hook_files=["other.mp4"],
                              body_files=["body.mp4"])
        self.assertEqual(caught.exception.code, "missing_hook")

    def test_pronoun_and_causal_dependencies_cannot_be_orphaned(self):
        hook = unit("h", "hook", "牙齿敏感怎么办？", topic="sensitivity", hook=True)
        orphan = unit("m", "explain", "它里面有这个成分。", topic="sensitivity",
                      requires=("sku:JXB-99",))
        product = unit("p", "product", "这是俊小白修护牙膏。", topic="",
                       introduces=("sku:JXB-99",))
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, orphan, product], sku_id="JXB-99", topic_id="sensitivity")
        self.assertEqual(caught.exception.code, "no_coherent_path")

    def test_other_product_and_old_promotion_are_rejected(self):
        hook = unit("h", "hook", "口气有异味吗？", topic="breath", sku="JXB-BREATH", hook=True)
        middle = unit("m", "explain", "刷牙也需要注意。", topic="breath", sku="JXB-BREATH")
        wrong_product = unit("p", "product", "这是俊小白色修牙膏。",
                             sku="JXB-COLOR", topic="whitening")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, middle, wrong_product],
                              sku_id="JXB-BREATH", topic_id="breath")
        self.assertEqual(caught.exception.code, "missing_product")
        self.assertIn("产品无法确认或不一致", caught.exception.details["rejections"])

        old = unit("p", "product", "618 三支79元，俊小白牙膏。", topic="",
                   sku="JXB-BREATH",
                   offer_status="stale", visual_offer_status="stale")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, middle, old], sku_id="JXB-BREATH", topic_id="breath")
        self.assertIn("画面含明确旧价促", caught.exception.details["rejections"])

    def test_variants_are_distinct_reproducible_and_do_not_repeat_takes(self):
        hook = unit("h", "hook", "牙齿敏感怎么办？", topic="sensitivity", hook=True)
        middle = unit("m", "explain", "先把问题说清楚。", topic="sensitivity")
        product_a = unit("p1", "product", "这是俊小白修护牙膏。", topic="",
                         introduces=("sku:JXB-99",))
        product_b = unit("p2", "product", "俊小白修护牙膏在这里。", topic="",
                         introduces=("sku:JXB-99",))
        candidates = [hook, middle, product_a, product_b]
        first = plan_semantic_mix(candidates, sku_id="JXB-99", topic_id="sensitivity",
                                  target_clips=3, variant_index=0)
        second = plan_semantic_mix(candidates, sku_id="JXB-99", topic_id="sensitivity",
                                   target_clips=3, variant_index=1)
        self.assertNotEqual([x["source_id"] for x in first["segments"]],
                            [x["source_id"] for x in second["segments"]])
        self.assertEqual(first, plan_semantic_mix(candidates, sku_id="JXB-99",
                                                  topic_id="sensitivity", target_clips=3))
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix(candidates, sku_id="JXB-99", topic_id="sensitivity",
                              target_clips=3, variant_index=2)
        self.assertEqual(caught.exception.code, "insufficient_variants")
        blocked = plan_semantic_mix(candidates, sku_id="JXB-99", topic_id="sensitivity",
                                    target_clips=3, exclude_keys=[("p1", 0.0, 2.0)])
        self.assertEqual(blocked["segments"][-1]["source_id"], "p2")

    def test_unknown_visual_status_allows_preview_but_reports_review(self):
        hook = unit("h", "hook", "怎么刷牙？", hook=True, visual_offer_status="unknown")
        middle = unit("m", "demonstrate", "每次只挤一点。")
        product = unit("p", "product", "这是俊小白修护牙膏。", topic="")
        plan = plan_semantic_mix([hook, middle, product], sku_id="99", topic_id="usage",
                                 target_clips=3)
        self.assertTrue(plan["needs_visual_review"])
        self.assertTrue(plan["warnings"])

    def test_same_source_must_move_forward_and_known_speakers_must_match(self):
        hook = unit("same", "hook", "牙齿敏感吗？", topic="sensitivity",
                    start=10, hook=True, speaker="想哥")
        earlier_middle = unit("same", "explain", "需要弄清原因。",
                              topic="sensitivity", start=0, speaker="想哥")
        product = unit("other", "product", "这是俊小白修护牙膏。", topic="",
                       speaker="想哥")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, earlier_middle, product], sku_id="99",
                              topic_id="牙齿敏感", target_clips=3)
        self.assertEqual(caught.exception.code, "no_coherent_path")

        later_middle = dict(earlier_middle, in_s=13.0, out_s=15.0, start=13.0)
        mixed_speaker = dict(later_middle, speaker="小如")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, mixed_speaker, product], sku_id="99",
                              topic_id="牙齿敏感", target_clips=3)
        self.assertEqual(caught.exception.code, "no_coherent_path")
        plan = plan_semantic_mix([hook, later_middle, product], sku_id="99",
                                 topic_id="牙齿敏感", target_clips=3)
        self.assertEqual([x["in_s"] for x in plan["segments"][:2]], [10, 13.0])

    def test_target_clip_count_cannot_be_filled_with_repeated_claim(self):
        hook = unit("h", "hook", "牙齿敏感吗？", topic="sensitivity", hook=True)
        first = unit("m1", "problem", "冷热酸甜都难受。", topic="sensitivity",
                     claim_key="pain")
        repeated = unit("m2", "explain", "冷热酸甜都难受。", topic="sensitivity",
                        claim_key="pain")
        product = unit("p", "product", "这是俊小白修护牙膏。", topic="")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, first, repeated, product], sku_id="JXB-99",
                              topic_id="sensitivity", target_clips=4)
        self.assertEqual(caught.exception.code, "no_coherent_path")

    def test_incomplete_asr_and_unreviewed_visual_promo_do_not_enter_plan(self):
        units = make_units(
            [{"start": 0, "end": 1.1, "text": "这个牙膏里面的", "role": "explain"}],
            source_id="a", path="修护.mp4", sku_id="JXB-99", topic_id="sensitivity",
            is_body_candidate=True,
        )
        self.assertFalse(units[0]["sentence_complete"])
        hook = unit("h", "hook", "牙齿敏感吗？", topic="sensitivity", hook=True)
        product = unit("p", "product", "这是俊小白修护牙膏。", topic="")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, units[0], product],
                              sku_id="JXB-99", topic_id="sensitivity")
        self.assertIn("句子边界不完整或句段过长", caught.exception.details["rejections"])

    def test_full_hook_keeps_every_sentence_without_mechanism_gate(self):
        cues = [
            {"start": 0.2, "end": 1.4, "text": "牙齿敏感吗？", "role": "hook"},
            {"start": 1.6, "end": 3.4, "text": "羟基磷灰石可以封堵牙小管。", "role": "explain"},
        ]
        sentences = make_units(cues, source_id="whole", path="JXB-99_牙敏.mp4",
                               is_hook_candidate=True)
        hook = make_full_hook_unit(sentences, full_duration_s=4.0)
        self.assertEqual((hook["in_s"], hook["out_s"]), (0.0, 4.0))
        self.assertEqual(hook["source_sentence_count"], 2)
        self.assertEqual(hook["text"], "牙齿敏感吗？羟基磷灰石可以封堵牙小管。")
        self.assertEqual(hook["pain_id"], "tooth_sensitivity")
        self.assertEqual(hook["mechanism_id"], "mineral_repair")
        self.assertTrue(hook["sentence_complete"])

        mixed = make_units(cues + [
            {"start": 3.5, "end": 3.8, "text": "直接干刷。", "role": "demonstrate"},
        ], source_id="mixed", path="JXB-99_牙敏.mp4", is_hook_candidate=True)
        self.assertTrue(make_full_hook_unit(mixed, full_duration_s=4.0)["sentence_complete"])

    def test_whole_hook_rejects_untranscribed_head_or_tail(self):
        head = make_units(
            [{"start": 3.1, "end": 4.2, "text": "牙齿敏感吗？"}],
            source_id="head", path="JXB-99_牙敏.mp4", is_hook_candidate=True,
        )
        self.assertFalse(make_full_hook_unit(head, full_duration_s=5.0)["sentence_complete"])

        tail = make_units(
            [{"start": 0.2, "end": 1.4, "text": "牙齿敏感吗？"}],
            source_id="tail", path="JXB-99_牙敏.mp4", is_hook_candidate=True,
        )
        self.assertFalse(make_full_hook_unit(tail, full_duration_s=4.5)["sentence_complete"])
        self.assertTrue(make_full_hook_unit(tail, full_duration_s=4.3)["sentence_complete"])

    def test_whole_hook_rejects_purchase_prompt_before_body(self):
        source = make_units([
            {"start": 0.0, "end": 1.0, "text": "牙齿敏感吗？"},
            {"start": 1.0, "end": 2.0, "text": "这是俊小白修护牙膏。"},
            {"start": 2.0, "end": 3.0, "text": "点头像购买。"},
        ], source_id="hook-with-cta", path="JXB-99_牙敏.mp4", is_hook_candidate=True)
        self.assertEqual([item["role"] for item in source], ["hook", "product", "cta"])
        self.assertFalse(make_full_hook_unit(source, full_duration_s=3.0)["sentence_complete"])

    def test_whole_hook_checks_original_ending_not_internal_asr_split(self):
        cues = [
            {"start": 0.1, "end": 1.1, "text": "牙齿敏感时因为这个"},
            {"start": 3.0, "end": 4.1, "text": "羟基磷灰石可以帮助护理牙齿。"},
        ]
        units = make_units(cues, source_id="gap", path="JXB-99_牙敏.mp4",
                           is_hook_candidate=True)
        self.assertFalse(units[0]["sentence_complete"])
        whole = make_full_hook_unit(units, full_duration_s=4.5)
        self.assertTrue(whole["sentence_complete"], whole["completeness_reasons"])

        dangling = make_units([
            {"start": 0.1, "end": 1.1, "text": "牙齿敏感吗？"},
            {"start": 1.2, "end": 2.0, "text": "因为这个"},
        ], source_id="dangling", path="JXB-99_牙敏.mp4", is_hook_candidate=True)
        broken = make_full_hook_unit(dangling, full_duration_s=2.2)
        self.assertFalse(broken["sentence_complete"])
        self.assertIn("Hook 末句未说完或句末无法确认完整",
                      broken["completeness_reasons"])

    def test_realistic_promo_hook_reports_cta_without_false_incomplete_reason(self):
        for final_line, offer_line in (
            ("真的啊 那我抓你去抢", "国庆拍一发六还送三支牙刷"),
            ("我这去给你多纯点", "现在国庆破架，拍一发酒"),
            ("那我抓进去墙", "国庆拍一发六还送三支软毛牙刷"),
        ):
            with self.subTest(final_line=final_line):
                cues = [
                    {"start": 0.2, "end": 1.0, "text": "黄牙怎么办？"},
                    {"start": 1.1, "end": 2.3, "text": offer_line},
                    {"start": 2.4, "end": 3.3, "text": final_line},
                ]
                units = make_units(cues, source_id=final_line, path="色修.mp4",
                                   is_hook_candidate=True)
                whole = make_full_hook_unit(units, full_duration_s=3.5)
                self.assertFalse(whole["sentence_complete"])
                self.assertFalse(any("末句未说完" in reason
                                     for reason in whole["completeness_reasons"]))
                self.assertTrue(any("促单引导" in reason
                                    for reason in whole["completeness_reasons"]))
                self.assertNotEqual(whole["offer_status"], "current")

    def test_two_hooks_keep_product_consistent_without_mechanism_gate(self):
        first = unit("h1", "hook", "牙齿敏感时，羟基磷灰石是什么？",
                     topic="sensitivity", hook=True, body=False)
        second = unit("h2", "hook", "冷热酸甜刺激，羟基磷灰石怎么护理？",
                      topic="sensitivity", hook=True, body=False)
        middle = unit("m", "explain", "因为羟基磷灰石可以封堵牙小管。",
                      topic="sensitivity")
        product = unit("p", "product", "这是俊小白修护牙膏。", topic="")
        plan = plan_semantic_mix([first, second, middle, product], sku_id="99",
                                 topic_id="sensitivity", target_clips=4)
        self.assertEqual([item["role"] for item in plan["segments"]],
                         ["hook", "hook", "explain", "product"])
        self.assertEqual(plan["hook_segment_count"], 2)
        self.assertEqual(plan["hook_duration_s"], 4.0)
        self.assertEqual(plan["pain_id"], "tooth_sensitivity")
        self.assertEqual(plan["mechanism_id"], "mineral_repair")

        incompatible = dict(second, text="牙齿敏感时，直接干刷怎么用？",
                            mechanism_id="dry_brushing")
        mixed_mechanism_plan = plan_semantic_mix(
            [first, incompatible, middle, product], sku_id="99",
            topic_id="sensitivity", target_clips=4)
        self.assertEqual(mixed_mechanism_plan["product_id"], "JXB-99")
        self.assertEqual(len(mixed_mechanism_plan["segments"]), 4)

    def test_product_identity_not_retail_sku_or_topic_is_the_consistency_gate(self):
        variant = unit("v", "explain", "羟基磷灰石可以帮助护理。",
                       sku="JXB-99-GIFT-PACK", product_id="JXB-99",
                       topic="gum", mechanism_id="mineral_repair")
        self.assertIsNone(unit_rejection_reason(
            variant, sku_id="JXB-99", topic_id="sensitivity",
            packaging_version="old-packaging"))
        other_product = dict(variant, product_id="JXB-COLOR")
        self.assertEqual(unit_rejection_reason(
            other_product, sku_id="JXB-99", topic_id="",
            packaging_version=""), "产品无法确认或不一致")

    def test_first_two_variants_diversify_hook_and_body_sources(self):
        hooks = [
            unit("hook-a", "hook", "俊小白修护牙膏怎么用？", duration=3.4,
                 hook=True, body=False),
            unit("hook-b", "hook", "这支俊小白修护牙膏怎么用？", duration=3.28,
                 hook=True, body=False),
        ]
        bodies = [
            unit("body-a", "demonstrate", "每次只挤一点牙膏。", duration=5.48),
            unit("body-b", "demonstrate", "刷牙时先少量取膏。", duration=5.16),
        ]
        first = plan_semantic_mix(hooks + bodies, sku_id="99", topic_id="",
                                  target_clips=2, target_duration_s=9,
                                  variant_index=0)
        second = plan_semantic_mix(hooks + bodies, sku_id="99", topic_id="",
                                   target_clips=2, target_duration_s=9,
                                   variant_index=1)
        self.assertEqual(first["available_variants"], 4)
        self.assertEqual({item["source_id"] for item in first["segments"]}
                         & {item["source_id"] for item in second["segments"]}, set())

    def test_hook_odor_prefers_fresh_breath_body_over_closer_usage_duration(self):
        hooks = [
            unit("hook-a", "hook",
                 "俊小白色修牙膏要用完了，你买了吗？不然我嘴里又有味了。",
                 sku="JXB-COLOR", duration=3.4, hook=True, body=False),
            unit("hook-b", "hook",
                 "俊小白色修牙膏要用完了，你买了吗？不然我嘴里又有味了。",
                 sku="JXB-COLOR", duration=3.28, hook=True, body=False),
        ]
        body_two = unit("body-2", "problem",
                        "关键是它加入了香氛口气清新技术，刷完牙打个嗝都是香香的。",
                        sku="JXB-COLOR", duration=5.48)
        body_three_usage = unit("body-3", "demonstrate",
                                "千万不要挤太多，就这么一点，直接干刷。",
                                sku="JXB-COLOR", duration=6.0)
        body_three_breath = unit("body-3", "problem",
                                 "关键是它加入了香氛口气清新技术，刷完牙打个嗝都是香香的。",
                                 sku="JXB-COLOR", start=39.8, duration=5.16)
        first = plan_semantic_mix(
            hooks + [body_two, body_three_usage, body_three_breath],
            sku_id="色修", topic_id="", target_clips=2,
            target_duration_s=9.0, variant_index=0)
        second = plan_semantic_mix(
            hooks + [body_two, body_three_usage, body_three_breath],
            sku_id="色修", topic_id="", target_clips=2,
            target_duration_s=9.0, variant_index=1)
        self.assertEqual(first["segments"][-1]["source_id"], "body-2")
        self.assertEqual(second["segments"][-1]["source_id"], "body-3")
        self.assertEqual(second["segments"][-1]["in_s"], 39.8)

    def test_planner_accepts_different_spoken_topics_for_same_product(self):
        hook = unit("h", "hook", "牙齿敏感怎么办？", topic="sensitivity",
                    hook=True, body=False)
        body = unit("b", "explain", "刷牙时先少量取膏。", topic="usage")
        product = unit("p", "product", "这是俊小白修护牙膏。", topic="")
        plan = plan_semantic_mix([hook, body, product], sku_id="99",
                                 topic_id="", target_clips=3)
        self.assertEqual(plan["product_id"], "JXB-99")

    def test_body_reference_needs_nearby_product_or_mechanism_antecedent(self):
        hook = unit("h", "hook", "牙齿敏感怎么办？", topic="sensitivity",
                    hook=True, body=False)
        dangling = unit("m", "explain", "这个成分能够改善问题。", topic="sensitivity")
        product = unit("p", "product", "这是俊小白修护牙膏。", topic="")
        with self.assertRaises(SemanticSelectionError) as caught:
            plan_semantic_mix([hook, dangling, product], sku_id="99",
                              topic_id="sensitivity", target_clips=3)
        self.assertEqual(caught.exception.code, "no_coherent_path")

    def test_product_named_in_whole_hook_can_lead_to_one_explanatory_body(self):
        hook = unit("h", "hook", "牙齿敏感怎么办？俊小白修护牙膏含羟基磷灰石。",
                    topic="sensitivity", hook=True, body=False, duration=8.0)
        explanation = unit("m", "explain", "核心就是羟基磷灰石封堵牙小管。",
                           topic="sensitivity", duration=6.0)
        plan = plan_semantic_mix([hook, explanation], sku_id="99",
                                 topic_id="sensitivity", target_clips=2,
                                 max_segments=2)
        self.assertEqual([x["role"] for x in plan["segments"]], ["hook", "explain"])
        self.assertEqual(plan["hook_duration_s"], 8.0)


if __name__ == "__main__":
    unittest.main()
