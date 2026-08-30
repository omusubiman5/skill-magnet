# Windows Explorer package leaf release evidence

## 文書の位置づけ

この文書は現行のExplorer統合だけを記録する正本です。旧「skillごとのleaf」方式の実験記録は現行仕様の証拠に使用しません。現行仕様は、ExplorerのDirectory/Backgroundごとに`Delivery Assurance`という単一package leafを表示し、その新規Codex Desktopタスク内でINDEXと全9スキルを読み、依頼に必要な最小集合を選ぶものです。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "release_scope": "one-package-leaf",
  "full_test_count": 136,
  "menu_leaf_count": 1,
  "selection_kinds": ["package"],
  "pack_skill_count": 9,
  "release_code_sha": "76397228e453fda6329dde50356b4e313594e538",
  "wheel_payload_sha256": "3d8b21f4397194c8908a1059511ca2676468ff93cf8dbc9cfd598cf8b1b12a9b",
  "automated_status": "LOCAL_136_PASS_RECEIPT_HARDENING_CI_PENDING",
  "windows_explorer_field_status": "PASS_INSTALLED_MENU_MANIFEST_DELIVERY_ASSURANCE_8F12AF5",
  "codex_desktop_result_status": "RETESTED_HANDOFF_NO_DISTINCT_TASK_OBSERVED"
}
explorer-results-ledger:end -->

- 統合テスト: `python -m unittest discover -s tests -v` — 136 tests PASS、環境依存1件skip
- menu contract: 1 package leaf / selection kind `package` / pack内9 skills
- 自動証拠: contract固定、INDEX/全SKILL materialization、source改変耐性、deep-link binding、CLI非起動、rollback、残留物回収
- 実機証拠: 更新wheelをWindowsへ再installし、modern context menuのstatusが`usable_installed_state: true`、`menu_contract_matches_config: true`を返した。実際のTSVはpack ID `codex-delivery-assurance`、表示名`Delivery Assurance`、固定commit `8f12af5ddfdd3b985f26d33dad09d6061d675342`を記録した。
- Desktop結果証拠: pack更新後にhandoffを再試験したが、試験後のtask一覧でdistinctな新規taskを観測できず、自然文回答も取得できなかった。以前の別名称・別固定commitの結果を転用しない。
- Desktop完了の扱い: 製品証拠の状態名はhandoffまでを表し、回答完了へ偽装しない。

## リリースゲート

`integration/explorer_results_gate.py`は、この台帳のtest count・leaf数・selection kind・pack skill数を、現在のテストsuiteと`skill-magnet.json`から得た値へ照合します。また、旧個別leaf仕様の文言が正本へ再混入した場合に失敗します。

Windows 0.3.4候補の最終受入では、次を新しい証拠として追記します。

1. wheelからのnative build、MSIX署名・install、Directory/Backgroundの単一`Delivery Assurance`表示。
2. package leafからCodex Desktop新規タスクを開き、materialized INDEX/全SKILLを読み、依頼に必要な部分集合だけを適用した自然文回答。
3. update、rollback、uninstall後のAppx・証明書・ContextMenu/rollback残留ゼロ。CIでは旧自己署名trust 2世代のupgrade cleanupも含めて確認済み。

macOS Finderは製品policy上のsupported adapterです。以前のrun `33259524623`は旧release probeに対するcomponent証拠であり、現行semantic E2Eの合格証拠へ転用しません。現行candidateでは実Quick Action install、`/usr/bin/automator`実行、pack検証、contract、INDEX/全skill materialization、Codex/Claude delivery境界、uninstall、残留ゼロをCIで再試験します。Finderのメニュー表示そのものは人手UX受入として別途必要です。

completion receiptを含む0.3.3 candidateのrun [`33306460511`](https://github.com/omusubiman5/skill-magnet/actions/runs/33306460511)はWindows/macOSともgreenである。両OSで132 testとstandalone wheel payload gateがPASSした。macOS jobは現行Finder semantic lifecycle、Windows jobは証明書state test、standalone wheelからのnative build、MSIX install/update/rollback/uninstall lifecycleまで通過した。このCI成功はDesktop自然文結果や人手Finderメニュー表示の代替ではない。
