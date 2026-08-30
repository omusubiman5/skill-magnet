# Windows Explorer package leaf release evidence

## 文書の位置づけ

この文書は現行のExplorer統合だけを記録する正本です。旧「skillごとのleaf」方式の実験記録は現行仕様の証拠に使用しません。現行仕様は、ExplorerのDirectory/Backgroundごとに`Delivery Assurance`という単一package leafを表示し、その新規Codex Desktopタスク内でINDEXと全9スキルを読み、依頼に必要な最小集合を選ぶものです。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "release_scope": "one-package-leaf",
  "full_test_count": 130,
  "menu_leaf_count": 1,
  "selection_kinds": ["package"],
  "pack_skill_count": 9,
  "release_code_sha": "3d388e3feded3ed00f76bb28e6a768efe646617c",
  "wheel_payload_sha256": "c8da150b48878b11dccb709902e93ebe05d8f360c83433787259aab9f921c2a1",
  "automated_status": "CI_33311945165_WINDOWS_MACOS_GREEN_130_PASS",
  "windows_explorer_field_status": "PASS_INSTALLED_MENU_MANIFEST_DELIVERY_ASSURANCE_8F12AF5",
  "codex_desktop_result_status": "HANDOFF_READY_ANSWER_COMPLETION_NOT_CLAIMED"
}
explorer-results-ledger:end -->

- 統合テスト: `python -m unittest discover -s tests -v` — 130 tests PASS、環境依存1件skip
- menu contract: 1 package leaf / selection kind `package` / pack内9 skills
- 自動証拠: contract固定、INDEX/全SKILL materialization、source改変耐性、deep-link binding、CLI非起動、rollback、残留物回収
- wheel再現性: 独立した2 directoryで0.4.0 wheelをbuildし、論理payload SHA-256が両方`c9a0ffe8f542fd475144ac8fecd284175a46863d69d1d44ec5be78ed901ba38f`で一致した。
- 0.4.1 path修正版も独立した2 directoryでbuildし、論理payload SHA-256が両方`c8da150b48878b11dccb709902e93ebe05d8f360c83433787259aab9f921c2a1`で一致した。
- 実機証拠: 更新wheelをWindowsへ再installし、modern context menuのstatusが`usable_installed_state: true`、`menu_contract_matches_config: true`を返した。実際のTSVはpack ID `codex-delivery-assurance`、表示名`Delivery Assurance`、固定commit `8f12af5ddfdd3b985f26d33dad09d6061d675342`を記録した。
- 0.4.0実install: package `SkillMagnet.ContextMenu_0.4.0.0_x64__byy1sc3mfzfz4`を登録し、1 package leaf、Directory/Background、署名済みcommand target、`usable_installed_state: true`を確認した。
- 0.4.1実install: package `SkillMagnet.ContextMenu_0.4.1.0_x64__byy1sc3mfzfz4`へ更新し、site-packages版が9 skillすべてをMarkdown-safeな ``C:/.../.skill-magnet...`` pathと正しいSHA-256でpromptへ出力することを確認した。
- Desktop handoff契約: promptはINDEX/全SKILL.mdの全文読了、最低1つのskill適用、実依頼の完了を必須化し、説明・一覧・準備確認だけでの終了を禁止する。
- Desktop完了の扱い: 製品証拠の状態名はhandoffまでを表し、回答完了へ偽装しない。completion receiptやcallbackは使わない。
- 課金境界: OpenAI/Anthropic API key、従量課金API、追加支払いを要求せず、既存のCodex Desktop/Claude利用プランへ渡す。

## リリースゲート

`integration/explorer_results_gate.py`は、この台帳のtest count・leaf数・selection kind・pack skill数を、現在のテストsuiteと`skill-magnet.json`から得た値へ照合します。また、旧個別leaf仕様の文言が正本へ再混入した場合に失敗します。

Windows 0.4.1候補の最終受入では、次を新しい証拠として追記します。

1. wheelからのnative build、MSIX署名・install、Directory/Backgroundの単一`Delivery Assurance`表示。
2. package leafからCodex Desktop新規タスクを開き、INDEX/全SKILLの読了と最低1つの適用を必須にしたpromptが損失なく渡ること。
3. update、rollback、uninstall後のAppx・証明書・ContextMenu/rollback残留ゼロ。CIでは旧自己署名trust 2世代のupgrade cleanupも含めて確認済み。

macOS Finderは製品policy上のsupported adapterです。以前のrun `33259524623`は旧release probeに対するcomponent証拠であり、現行semantic E2Eの合格証拠へ転用しません。現行candidateでは実Quick Action install、`/usr/bin/automator`実行、pack検証、contract、INDEX/全skill materialization、Codex/Claude delivery境界、uninstall、残留ゼロをCIで再試験します。Finderのメニュー表示そのものは人手UX受入として別途必要です。

0.3.3 candidateのrun [`33306460511`](https://github.com/omusubiman5/skill-magnet/actions/runs/33306460511)は過去設計の参考記録であり、0.4.0のrelease証拠には使用しない。

terminal cleanup hardeningを含む0.3.6 candidateのrun [`33307964947`](https://github.com/omusubiman5/skill-magnet/actions/runs/33307964947)も過去版の証拠である。0.4.0ではno-API handoff契約、129 test、standalone wheel、macOS Finder lifecycle、Windows certificate/native/MSIX lifecycleを改めて実行する。

0.4.0 candidateのrun [`33310087151`](https://github.com/omusubiman5/skill-magnet/actions/runs/33310087151)はWindows/macOSともgreenである。両OSで129 testとstandalone wheel payload gateがPASSした。macOSでは実Quick Action install、Automator実行、Codex/Claude handoff意味論、uninstall、残留ゼロを確認した。Windowsではcertificate state、installed wheelからのnative build、MSIX install/update/rollback/uninstall lifecycleを確認した。

0.4.1 path修正版のrun [`33311945165`](https://github.com/omusubiman5/skill-magnet/actions/runs/33311945165)もWindows/macOSともgreenである。両OS130 test、standalone wheel payload gate、macOS Finder lifecycle、Windows certificate/native/MSIX lifecycleがPASSした。
