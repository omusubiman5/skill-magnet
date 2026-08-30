# Windows Explorer package leaf release evidence

## 文書の位置づけ

この文書は現行のExplorer統合だけを記録する正本です。旧「skillごとのleaf」方式の実験記録は現行仕様の証拠に使用しません。現行仕様は、ExplorerのDirectory/Backgroundごとに`Delivery Assurance`という単一package leafを表示し、その新規Codex Desktopタスク内でINDEXと全9スキルを読み、依頼に必要な最小集合を選ぶものです。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "release_scope": "one-package-leaf",
  "full_test_count": 127,
  "menu_leaf_count": 1,
  "selection_kinds": ["package"],
  "pack_skill_count": 9,
  "release_code_sha": "df8e15f72924166a99c360e2280c36f281eeaa79",
  "wheel_payload_sha256": "de2be0d0415e398369ae1464f0b8759f401863ac3f07c56da0df95b4bbd52a86",
  "automated_status": "NOT_RUN_AFTER_COMPLETION_REMEDIATION",
  "windows_explorer_field_status": "PASS_INSTALLED_MENU_MANIFEST_DELIVERY_ASSURANCE_8F12AF5",
  "codex_desktop_result_status": "NOT_RETESTED_AFTER_PACK_UPDATE"
}
explorer-results-ledger:end -->

- 統合テスト: `python -m unittest discover -s tests -v` — 127 tests PASS、環境依存1件skip
- menu contract: 1 package leaf / selection kind `package` / pack内9 skills
- 自動証拠: contract固定、INDEX/全SKILL materialization、source改変耐性、deep-link binding、CLI非起動、rollback、残留物回収
- 実機証拠: 更新wheelをWindowsへ再installし、modern context menuのstatusが`usable_installed_state: true`、`menu_contract_matches_config: true`を返した。実際のTSVはpack ID `codex-delivery-assurance`、表示名`Delivery Assurance`、固定commit `8f12af5ddfdd3b985f26d33dad09d6061d675342`を記録した。
- Desktop結果証拠: pack更新後の新規Codex Desktop自然文回答は未再試験であり、以前の別名称・別固定commitの結果を転用しない。
- Desktop完了の扱い: 製品証拠の状態名はhandoffまでを表し、回答完了へ偽装しない。

## リリースゲート

`integration/explorer_results_gate.py`は、この台帳のtest count・leaf数・selection kind・pack skill数を、現在のテストsuiteと`skill-magnet.json`から得た値へ照合します。また、旧個別leaf仕様の文言が正本へ再混入した場合に失敗します。

Windows 0.3.2候補の最終受入では、次を新しい証拠として追記します。

1. wheelからのnative build、MSIX署名・install、Directory/Backgroundの単一`Delivery Assurance`表示。
2. package leafからCodex Desktop新規タスクを開き、materialized INDEX/全SKILLを読み、依頼に必要な部分集合だけを適用した自然文回答。
3. update、rollback、uninstall後のAppx・証明書・ContextMenu/rollback残留ゼロ。CIでは旧自己署名trust 2世代のupgrade cleanupも含めて確認済み。

macOS Finderはcommunity betaです。固定artifact-input commit `df8e15f…`とcanonical wheel payload `de2be0d…`に対し、run `33259524623`で125 test、wheel gate、実Quick Action install、`/usr/bin/automator`実行、製品adapter到達、uninstall、残留ゼロがgreenです。Finderのメニュー表示そのものは人手UX受入であり、このWindows証拠を転用しません。
