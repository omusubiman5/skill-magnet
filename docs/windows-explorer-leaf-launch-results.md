# Windows Explorer package leaf release evidence

## 文書の位置づけ

この文書は現行のExplorer統合だけを記録する正本です。旧「skillごとのleaf」方式の実験記録は現行仕様の証拠に使用しません。現行仕様は、ExplorerのDirectory/Backgroundごとに`Delivery Assurance`という単一package leafを表示し、その新規Codex Desktopタスク内でINDEXと全9スキルを読み、依頼に必要な最小集合を選ぶものです。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "release_scope": "one-package-leaf",
  "release_version": "0.5.2",
  "distribution_scope": "local-self-signed",
  "full_test_count": 146,
  "menu_leaf_count": 1,
  "selection_kinds": ["package"],
  "pack_skill_count": 9,
  "release_code_sha": "6f5f1b7d39a653ef229b05ca45e8463d6bb0e16b",
  "wheel_payload_sha256": "c79ffa8f81b9f086dc7bdaace4a357b7b8108dc1bfad88ff030b6555fb7b3a3f",
  "automated_status": "LOCAL_RELEASE_GATE_PASS_146",
  "windows_explorer_field_status": "PASS_REAL_RIGHT_CLICK_MENU_AND_CONFIRMATION_UI_0_5_1",
  "macos_finder_field_status": "CI_SEMANTIC_ONLY_REAL_UI_NOT_CLAIMED_FOR_0_5_2",
  "public_distribution_status": "NOT_CLAIMED_REQUIRES_EXTERNAL_PUBLISHER",
  "codex_desktop_result_status": "HANDOFF_READY_ANSWER_COMPLETION_NOT_CLAIMED"
}
explorer-results-ledger:end -->

- 統合テスト: `python -m unittest discover -s tests -v` — 146 tests PASS、環境依存1件skip
- menu contract: 1 package leaf / selection kind `package` / pack内9 skills
- 自動証拠: contract固定、GitHub固定commitのINDEX/全SKILL参照、archiveのメモリ内検証、deep-link binding、ローカルskill残留ゼロ
- wheel再現性: 独立した2 directoryで0.4.0 wheelをbuildし、論理payload SHA-256が両方`c9a0ffe8f542fd475144ac8fecd284175a46863d69d1d44ec5be78ed901ba38f`で一致した。
- 0.4.1 path修正版も独立した2 directoryでbuildし、論理payload SHA-256が両方`c8da150b48878b11dccb709902e93ebe05d8f360c83433787259aab9f921c2a1`で一致した。
- 0.5.0 GitHub-only候補wheelの論理payload SHA-256は`7c2f2a75daf640ffb36c42a9840211b1dd82463e9673ade47223ab7becdff5f9`、wheel file SHA-256は`99addeeafde2933af9feab15848e90d835518fe5eba15ad1d0c714eba460eca4`である。
- 0.5.1ローカル自己署名版wheelの論理payload SHA-256は`c463dc6bacaa10dd8959e3e5c63119a021073f56f5d6de8d1c0c652e1d20be32`、wheel file SHA-256は`1335abceda94b20955ebf877330695e41dac6a84530c0a0a788b5e5eee0e8fda`である。公開publisherによる配布は本台帳のGO対象に含めない。
- 0.5.2ローカル導入候補wheelの論理payload SHA-256は`c96f964a9799de4fec3240797f4f2b07dae4f3920d531a7632cbcf7651a3ce9d`、wheel file SHA-256は`8082dfa1d0c21ca505e8cd1a5fbcc96c6faccd938db4319ceb6a0e461e5c23a2`である。macOSの実Finder UIは友人実機受入まで未主張とする。
- Skill Library Manager実装後の0.5.2候補wheelは、Windows/macOS CIで共通の論理payload SHA-256 `c73348437122644e57b16ece587667b7dca1850ed2f370c7c38331c8a7ccf178`をrelease gateに固定する。
- READMEへSkill Library Manager関連文書への導線を追加した0.5.2候補wheelは、Windows/macOS CIで共通の論理payload SHA-256 `9dfb351b6a3416c7b4e3e82d4fe9064bd1af29473f98c527e1293e960b2900ff`をrelease gateに固定する。
- Claude実行先を公式`claude://code/new`経路へ変更し、Codex Desktopアプリ／Claude Codeデスクトップアプリの二ターゲットを明記した0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `c79ffa8f81b9f086dc7bdaace4a357b7b8108dc1bfad88ff030b6555fb7b3a3f`を確認した。release codeは`6f5f1b7d39a653ef229b05ca45e8463d6bb0e16b`である。
- 実機証拠: 0.5.1 wheelをWindowsへinstallし、modern context menuのstatusが`usable_installed_state: true`、`menu_contract_matches_config: true`、`menu_leaf_count: 1`を返した。File Explorerの`C:\Projects\skill-magnet`背景を実際に右クリックし、`Skill Magnet`→`Delivery Assurance`から`Skill Magnet — 実行確認`画面が起動すること、画面上のproject、pack、用途、実行AI、依頼内容、実行/取消UIを確認した。外部AIへのテスト依頼送信はフィールドUI受入の対象外とし、確認画面を取消で閉じた。実際のTSVはpack ID `codex-delivery-assurance`、表示名`Delivery Assurance`、固定commit `8f12af5ddfdd3b985f26d33dad09d6061d675342`を記録した。
- 0.4.0実install: package `SkillMagnet.ContextMenu_0.4.0.0_x64__byy1sc3mfzfz4`を登録し、1 package leaf、Directory/Background、署名済みcommand target、`usable_installed_state: true`を確認した。
- 0.4.1実installのローカルskill path方式は廃止対象の過去実績であり、現行合格証拠には使用しない。現行版はGitHub固定commit URLとSHA-256だけをpromptへ出力する。
- Desktop handoff契約: promptは全SKILL.mdと存在する場合だけINDEXの全文読了、最低1つのskill規則の実作業への適用、実依頼の完了を必須化し、読了・説明・一覧・準備確認だけでの終了を禁止する。
- Windows実E2E: File Explorerで`C:\Projects\skill-magnet`を右クリックし、`Skill Magnet`→`Delivery Assurance`→Codex選択→依頼入力→起動確認→handoffまで完走した。contract `8119b643c2de4ae7b6d9375aff2e2116`、prompt SHA-256 `c6614823868c353d994fa7b08aec820c59044976758227cb426e39b65e639dc9`。
- Desktop完了の扱い: 製品証拠の状態名はhandoffまでを表し、回答完了へ偽装しない。completion receiptやcallbackは使わない。
- 課金境界: OpenAI/Anthropic API key、従量課金API、追加支払いを要求せず、既存のCodex Desktop/Claude利用プランへ渡す。

## リリースゲート

`integration/explorer_results_gate.py`は、この台帳のtest count・leaf数・selection kind・pack skill数を、現在のテストsuiteと`skill-magnet.json`から得た値へ照合します。また、旧個別leaf仕様の文言が正本へ再混入した場合に失敗します。

Windows 0.5.1ローカル自己署名版では、次をリリース証拠として固定した。

1. wheelからのnative build、MSIX署名・install、Directory/Backgroundの単一`Delivery Assurance`契約と、Explorer実UIでの単一leaf表示。
2. package leafから実行確認画面が起動し、全SKILL、任意INDEX、最低1つのskill規則の実作業適用を必須にしたhandoff契約が表示対象packへ結び付くこと。
3. update、rollback、uninstall後のAppx・証明書・ContextMenu/rollback残留ゼロ。CIでは旧自己署名trust 2世代のupgrade cleanupも含めて確認済み。

macOS Finderは製品policy上のsupported adapterです。配布形態は[`macos-local-install-policy.md`](macos-local-install-policy.md)で定めるPythonライブラリ＋Finder Quick Actionのローカル導入版であり、Developer ID、notarization、Mac App Storeを要件にしません。以前のrun `33259524623`は旧release probeに対するcomponent証拠であり、現行semantic E2Eの合格証拠へ転用しません。現行candidateでは実Quick Action install、`/usr/bin/automator`実行、GitHub固定commitのメモリ内検証、contract、Codex/Claude delivery境界、uninstall、ローカルskill残留ゼロをCIで再試験します。Finderのメニュー表示そのものは[`macos-finder-friend-acceptance.md`](macos-finder-friend-acceptance.md)による友人の実Mac受入を必須とし、CIで代替しません。

0.3.3 candidateのrun [`33306460511`](https://github.com/omusubiman5/skill-magnet/actions/runs/33306460511)は過去設計の参考記録であり、0.4.0のrelease証拠には使用しない。

terminal cleanup hardeningを含む0.3.6 candidateのrun [`33307964947`](https://github.com/omusubiman5/skill-magnet/actions/runs/33307964947)も過去版の証拠である。0.4.0ではno-API handoff契約、129 test、standalone wheel、macOS Finder lifecycle、Windows certificate/native/MSIX lifecycleを改めて実行する。

0.4.0 candidateのrun [`33310087151`](https://github.com/omusubiman5/skill-magnet/actions/runs/33310087151)はWindows/macOSともgreenである。両OSで129 testとstandalone wheel payload gateがPASSした。macOSでは実Quick Action install、Automator実行、Codex/Claude handoff意味論、uninstall、残留ゼロを確認した。Windowsではcertificate state、installed wheelからのnative build、MSIX install/update/rollback/uninstall lifecycleを確認した。

0.4.1 path修正版のrun [`33311945165`](https://github.com/omusubiman5/skill-magnet/actions/runs/33311945165)もWindows/macOSともgreenである。両OS130 test、standalone wheel payload gate、macOS Finder lifecycle、Windows certificate/native/MSIX lifecycleがPASSした。
