# Windows Explorer package leaf release evidence

## 文書の位置づけ

この文書は現行のExplorer統合だけを記録する正本です。旧「skillごとのleaf」方式の実験記録は現行仕様の証拠に使用しません。現行仕様は、ExplorerのDirectory/Backgroundごとに`PMO`という単一package leafを表示し、その新規Codex Desktopタスク内でINDEXと全9スキルを読み、依頼に必要な最小集合を選ぶものです。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "release_scope": "one-package-leaf",
  "full_test_count": 122,
  "menu_leaf_count": 1,
  "selection_kinds": ["package"],
  "pack_skill_count": 9,
  "release_code_sha": "136539e3f80b245dee5baac617a1d41708d39018",
  "wheel_payload_sha256": "8094ae4da399e0a97eb5c00241afc3818bf2c7ce16f17c87d10f4c1f42d34694",
  "automated_status": "PASS",
  "windows_explorer_field_status": "PASS_0.3.2_SMART_APP_CONTROL_REAL_UI_TO_CONFIRMATION",
  "codex_desktop_result_status": "PASS_USER_CONFIRMED_NATURAL_LANGUAGE_RESULT"
}
explorer-results-ledger:end -->

- 統合テスト: `python -m unittest discover -s tests -v` — 122 tests PASS
- menu contract: 1 package leaf / selection kind `package` / pack内9 skills
- 自動証拠: contract固定、INDEX/全SKILL materialization、source改変耐性、deep-link binding、CLI非起動、rollback、残留物回収
- 実機証拠: Smart App Control有効Windows 11で0.3.2のfolder backgroundを右クリック→`Skill Magnet`→単一`PMO`→`Skill Magnet — 実行確認`を実操作した。検証時間帯のSkill Magnet関連Code Integrity 3033/3077は0件で、invoke logは`create_process_succeeded`を記録した。0.3.0の4551証拠は失敗履歴として保持し、現行PASSへ転用しない。
- Desktop結果証拠: ユーザーが新規Codexタスクの自然文回答を確認した。回答はBEADSのMarkdown 2件へ具体的なrelease blockerを提示し、INDEXに従ってPMOパックのローカルread-only・途中承認なし・公式Webのみの境界を組み合わせ、対象外のsubagent/CI patch/認証設計を適用しなかったことを説明した。ファイル変更なしという依頼境界も維持した。
- Desktop完了の扱い: 製品証拠の状態名は引き続きhandoffまでを表し、回答完了へ偽装しない。今回の自然文結果はユーザーによる別個の実機受入証拠として記録する。

## リリースゲート

`integration/explorer_results_gate.py`は、この台帳のtest count・leaf数・selection kind・pack skill数を、現在のテストsuiteと`skill-magnet.json`から得た値へ照合します。また、旧個別leaf仕様の文言が正本へ再混入した場合に失敗します。

Windows 0.3.2候補の最終受入では、次を新しい証拠として追記します。

1. wheelからのnative build、MSIX署名・install、Directory/Backgroundの単一`PMO`表示。
2. package leafからCodex Desktop新規タスクを開き、materialized INDEX/全SKILLを読み、依頼に必要な部分集合だけを適用した自然文回答。
3. update、rollback、uninstall後のAppx・証明書・ContextMenu/rollback残留ゼロ。CIでは旧自己署名trust 2世代のupgrade cleanupも含めて確認済み。

macOS Finderはcommunity betaです。macOS runner上の実Quick Action install、`/usr/bin/automator`実行、通常の製品`context` adapter到達、uninstall、残留ゼロはrelease code SHA `b4f68209…`のrun `33248318073`で独立に確認済みです。Finderのメニュー表示そのものは人手UX受入であり、このWindows証拠を転用しません。
