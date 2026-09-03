# Windows Explorer package leaf release evidence

## 文書の位置づけ

この文書は現行のExplorer統合だけを記録する正本です。旧「skillごとのleaf」方式の実験記録は現行仕様の証拠に使用しません。現行仕様は、ExplorerのDirectory/Backgroundごとに有効なpackを`Skill Pack: <表示名>`として1項目ずつ表示し、固定の`Library Manager`管理actionを加えます。各package leafは新規Codex Desktopタスク内でpackのINDEXと全skillを読み、依頼に必要な最小集合を選びます。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "release_scope": "package-leaves",
  "release_version": "0.5.4",
  "distribution_scope": "local-self-signed",
  "full_test_count": 168,
  "menu_leaf_count": 3,
  "menu_action_count": 4,
  "library_manager_entry_count": 1,
  "selection_kinds": ["package"],
  "pack_skill_counts": [9, 9, 12],
  "release_code_sha": "3000d3c89284992faadd3f053631530c4f29b1c4",
  "wheel_payload_sha256": "f300d219367c1deda0e7d6b9281bfc04e04eafbaa0c8f930133abc6b1bdd8651",
  "automated_status": "LOCAL_RELEASE_GATE_PASS_168",
  "windows_explorer_field_status": "PASS_REAL_RIGHT_CLICK_MENU_AND_CONFIRMATION_UI_0_5_1",
  "macos_finder_field_status": "CI_SEMANTIC_ONLY_REAL_UI_NOT_CLAIMED_FOR_0_5_2",
  "public_distribution_status": "NOT_CLAIMED_REQUIRES_EXTERNAL_PUBLISHER",
  "codex_desktop_result_status": "HANDOFF_READY_ANSWER_COMPLETION_NOT_CLAIMED"
}
explorer-results-ledger:end -->

- 統合テスト: `python -m unittest discover -s tests -v` — 168 tests PASS、環境依存1件skip
- menu contract: active 3 package leaves / `Library Manager` 1 fixed action / selection kind `package` / pack別skill数 `[9, 9, 12]`
- 自動証拠: contract固定、GitHub固定commitのINDEX/全SKILL参照、archiveのメモリ内検証、deep-link binding、ローカルskill残留ゼロ
- wheel再現性: 独立した2 directoryで0.4.0 wheelをbuildし、論理payload SHA-256が両方`c9a0ffe8f542fd475144ac8fecd284175a46863d69d1d44ec5be78ed901ba38f`で一致した。
- 0.4.1 path修正版も独立した2 directoryでbuildし、論理payload SHA-256が両方`c8da150b48878b11dccb709902e93ebe05d8f360c83433787259aab9f921c2a1`で一致した。
- 0.5.0 GitHub-only候補wheelの論理payload SHA-256は`7c2f2a75daf640ffb36c42a9840211b1dd82463e9673ade47223ab7becdff5f9`、wheel file SHA-256は`99addeeafde2933af9feab15848e90d835518fe5eba15ad1d0c714eba460eca4`である。
- 0.5.1ローカル自己署名版wheelの論理payload SHA-256は`c463dc6bacaa10dd8959e3e5c63119a021073f56f5d6de8d1c0c652e1d20be32`、wheel file SHA-256は`1335abceda94b20955ebf877330695e41dac6a84530c0a0a788b5e5eee0e8fda`である。公開publisherによる配布は本台帳のGO対象に含めない。
- 0.5.2ローカル導入候補wheelの論理payload SHA-256は`c96f964a9799de4fec3240797f4f2b07dae4f3920d531a7632cbcf7651a3ce9d`、wheel file SHA-256は`8082dfa1d0c21ca505e8cd1a5fbcc96c6faccd938db4319ceb6a0e461e5c23a2`である。macOSの実Finder UIは友人実機受入まで未主張とする。
- Skill Library Manager実装後の0.5.2候補wheelは、Windows/macOS CIで共通の論理payload SHA-256 `c73348437122644e57b16ece587667b7dca1850ed2f370c7c38331c8a7ccf178`をrelease gateに固定する。
- READMEへSkill Library Manager関連文書への導線を追加した0.5.2候補wheelは、Windows/macOS CIで共通の論理payload SHA-256 `9dfb351b6a3416c7b4e3e82d4fe9064bd1af29473f98c527e1293e960b2900ff`をrelease gateに固定する。
- Claude実行先を公式`claude://code/new`経路へ変更し、Codex Desktopアプリ／Claude Codeデスクトップアプリの二ターゲットを明記した0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `c79ffa8f81b9f086dc7bdaace4a357b7b8108dc1bfad88ff030b6555fb7b3a3f`を確認した。release codeは`6f5f1b7d39a653ef229b05ca45e8463d6bb0e16b`である。
- Skill Library Manager右クリック入口を追加した0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `ac29c9cfeef64f3dfda63c46d32641b222b0d1f90d000d83c831888e8b1e684b`を確認した。release codeは`202f16e649cc0ef3146ee554cdc136226eb668f0`である。
- Skill Library Managerの7画面説明画像とREADME操作ガイドを追加した0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `fbfcdc930714dd9bee82fc8eea06f47bae0eca0ff88fb9cc560db1bedc63510e`を確認した。release codeは`e85975e998555eae84073288db4f4baac81b571e`である。
- 作業用repositoryをアプリ専用stateへ移し、`Draft directory`入力を廃止して初心者向け7画面ガイドを更新した0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `337c81ac557ceb0109993a09416c08f5bec44f4b9757e5a06110568826184dcb`を確認した。release codeは`8232fdedb892d15a89f2181ecff4f8f66b15e877`である。
- Skill Library Managerを通常1画面・最大2画面へ整理し、標準skill folderの自動import、既存GitHub URL表示、validationのエラー化、OS自動判定、Publish画面内activationを実装した0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `93adcf89a0074673bd5a42f8fe760e81fc613618fa4433551272ece47b5be870`を確認した。release codeは`4e2a2557ff73df69e1bb676ccf9916c20d4e9dcc`である。
- Skill画面の用途を「スキルを作る」から「作成済みスキルを登録する」へ訂正した0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `7e4b9e6268e9d8d672040c1da3da1d5ef0849fb0aa20ae16c11389fcb7363900`を確認した。release codeは`963bc813a16c2f93ee9eb0a818cabc81230b3b73`である。
- 手動登録で作成済みスキルfolderを必須化し、画面内でのskill新規作成を拒否する0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `e78b33b6fe60c77c46b527e3bc21592025ae9d38bff6849b1cdc026bd03f7187`を確認した。release codeは`a0f684aa08807e48a2ad999849b4e13b918440a7`である。
- Skill IDとPack IDの入力欄を廃止し、SKILL.mdとpack表示名から内部キーを自動決定する0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `109e0915d14b82c736031fbf97f09023b36ace554eceb9e7d93ece69832a1fbd`を確認した。release codeは`061c85d9a8a4ab27d91cb9fe6d0cf38eafc447c8`である。
- Skill Library Managerをタブのない1画面へ統合し、手動登録をfolder指定だけに限定して`SKILL.md`／`acceptance.json`不足を登録前に拒否する0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `0bc22e25db24ebd02ebb3a0502edc39a4d123e5a98e76b8dcd09ab1304722df3`を確認した。release codeは`c8067f801f433e4913a29db525ef162548eca170`である。
- 右クリック項目を`Library Manager`、`Skill Pack: <表示名>`、`Skill: <表示名>`へ分類した0.5.2候補wheelは、独立した2 buildで共通の論理payload SHA-256 `1a34219857a02b20ce3a3623c26e3891aa745ccd1dae99470dc6a27639f589cb`を確認した。release codeは`d92e0797a99988a21541edc4f89a82ee0c7a5b4a`である。
- 単一skill、1 pack、複数pack collectionをfolder 1つから自動判定する候補wheelは、独立した2 buildで共通の論理payload SHA-256 `fe05738adb37df56619c1d56decea7b79d131209cfee93bf072b1b41ad4b3ac9`を確認した。release codeは`cb79ca9a7907909cb5e5562fd803ef6dc761ec46`である。実`C:\Projects\cangjie-skill-clean\books`の母集合3 pack・33 skillに対し、検出・登録・catalog収録はいずれも33件、欠落0件だった。
- Library Managerの後続3ボタンと確認checkboxを廃止し、現在可能な操作だけへ切り替わる1ボタンに統合した候補wheelは、独立した2 buildで共通の論理payload SHA-256 `21a4d5d3473ef62215519f80e151ed897d09b7ce29255dc282d66329c88be5e0`を確認した。release codeは`f9d64fe22ffeba7a99abbb27e74dd5c826315a34`である。
- 登録済みpackの手動再選択を冪等なno-opにした候補wheelは、独立した2 buildで共通の論理payload SHA-256 `d6103119acede635c07fa938e9c0dbf5fc8bf504d8391681d7a21dfffe799dc6`を確認した。release codeは`857bf42910bbf0bd357d2737b1b6835aeda4ca2f`である。実ユーザーstateの`codex-cli` 9 skillを再選択し、`already_registered=true`とcatalog SHA-256不変を確認した。
- Library ManagerのWindowsアクセス拒否、中断復旧、remote既存ファイル保護を修正した。remote verifierは毎回固有directoryを使い、cleanup失敗を完了結果と分離する。GUI／CLIから同一transactionの再開またはローカル破棄を選べ、管理対象外ファイルを保持し、削除差分を送信前に拒否する。危険な削除を含んでいた既存PR #1はCLOSED、transaction `0d954338e09c4a97ad19639e69d5298a`は`abandoned`へ移行し、remote branchは証拠と利用者管理のため保持した。独立した2 buildの論理payload SHA-256はともに`50a8e34971809707268945270ef249da893e4d7a8e5153f872c85067237b3f71`で一致した。
- 0.5.3ではPR OPENを正常な`waiting_for_merge`として扱い、remote副作用後のlocal-only破棄、同一library／remoteの重複transaction、差分0件のPR作成、操作ボタンの二重実行を禁止した。重複PR #2はCLOSED、継続対象PR #3はOPENのまま保持した。独立した2 buildの論理payload SHA-256はともに`b74fb3843339667f1afe917082d4cda021e73c82d84451f341a032d41d7a351a`で一致した。release codeは`dad49a227f57abe3d2246196db293b27d31e62a9`である。
- 0.5.4ではLibrary Managerへpack→skill一覧とCRUDを追加し、管理対象ファイルだけの削除公開、依存削除防止、隔離候補rollback、同一repository旧packの有効設定除去を実装した。168 tests PASS、環境依存1件skip。独立した2 buildの論理payload SHA-256はともに`84b9e81156b3e4bbdffa609788742e2cb47cfb1b1051e70bbfb9b25933566202`で一致した。
- Library Manager transaction `8dc76704a259400e9b0a2259612155ce`を完走し、skill保管庫PR #3をmerge、merge commitのmanifestを再検証して`codex-cli`と`conflict-clarity`を有効化した。現行menuは3 package leavesとLibrary Managerの計4 actionで、`menu_contract_matches_config: true`、`usable_installed_state: true`である。
- 3 pack有効化後の0.5.3 wheelは、独立した2 buildで論理payload SHA-256 `f300d219367c1deda0e7d6b9281bfc04e04eafbaa0c8f930133abc6b1bdd8651`が一致した。release codeは`3000d3c89284992faadd3f053631530c4f29b1c4`である。
- 実機証拠: 0.5.1 wheelをWindowsへinstallし、modern context menuのstatusが`usable_installed_state: true`、`menu_contract_matches_config: true`、`menu_leaf_count: 1`を返した。File Explorerの`C:\Projects\skill-magnet`背景を実際に右クリックし、`Skill Magnet`→`Delivery Assurance`から`Skill Magnet — 実行確認`画面が起動すること、画面上のproject、pack、用途、実行AI、依頼内容、実行/取消UIを確認した。外部AIへのテスト依頼送信はフィールドUI受入の対象外とし、確認画面を取消で閉じた。実際のTSVはpack ID `codex-delivery-assurance`、表示名`Delivery Assurance`、固定commit `8f12af5ddfdd3b985f26d33dad09d6061d675342`を記録した。
- Skill Library Manager右クリック実機証拠: 現行sourceからWindows 11 modern拡張をbuild・再登録し、`C:\Projects\skill-magnet` folderを実際に右クリックした。`Skill Magnet`配下に`Skill Library Manager`と`Delivery Assurance`が表示され、manager選択でGUIが起動した。標準構成のskill folderでは自動import後にPublishだけを表示し、作成済みskillの手動登録時だけSkillとPublishを表示する。作業用repository、catalog、INDEX、validation、OS判定は自動管理する。statusは`menu_contract_matches_config: true`、package leaf 1件、manager action 1件、合計2 action、`usable_installed_state: true`を返した。
- 0.4.0実install: package `SkillMagnet.ContextMenu_0.4.0.0_x64__byy1sc3mfzfz4`を登録し、1 package leaf、Directory/Background、署名済みcommand target、`usable_installed_state: true`を確認した。
- 0.4.1実installのローカルskill path方式は廃止対象の過去実績であり、現行合格証拠には使用しない。現行版はGitHub固定commit URLとSHA-256だけをpromptへ出力する。
- Desktop handoff契約: promptは全SKILL.mdと存在する場合だけINDEXの全文読了、最低1つのskill規則の実作業への適用、実依頼の完了を必須化し、読了・説明・一覧・準備確認だけでの終了を禁止する。
- Windows実E2E: File Explorerで`C:\Projects\skill-magnet`を右クリックし、`Skill Magnet`→`Delivery Assurance`→Codex選択→依頼入力→起動確認→handoffまで完走した。contract `8119b643c2de4ae7b6d9375aff2e2116`、prompt SHA-256 `c6614823868c353d994fa7b08aec820c59044976758227cb426e39b65e639dc9`。
- Desktop完了の扱い: 製品証拠の状態名はhandoffまでを表し、回答完了へ偽装しない。completion receiptやcallbackは使わない。
- 課金境界: OpenAI/Anthropic API key、従量課金API、追加支払いを要求せず、既存のCodex Desktop/Claude利用プランへ渡す。

## リリースゲート

`integration/explorer_results_gate.py`は、この台帳のtest count・leaf数・selection kind・pack skill数を、現在のテストsuiteと`skill-magnet.json`から得た値へ照合します。また、旧個別leaf仕様の文言が正本へ再混入した場合に失敗します。

Windows 0.5.1ローカル自己署名版では、次をリリース証拠として固定した。

1. wheelからのnative build、MSIX署名・install、Directory/Backgroundの単一`Skill Pack: Delivery Assurance`契約と、Explorer実UIでの単一leaf表示。
2. package leafから実行確認画面が起動し、全SKILL、任意INDEX、最低1つのskill規則の実作業適用を必須にしたhandoff契約が表示対象packへ結び付くこと。
3. update、rollback、uninstall後のAppx・証明書・ContextMenu/rollback残留ゼロ。CIでは旧自己署名trust 2世代のupgrade cleanupも含めて確認済み。

macOS Finderは製品policy上のsupported adapterです。配布形態は[`macos-local-install-policy.md`](macos-local-install-policy.md)で定めるPythonライブラリ＋Finder Quick Actionのローカル導入版であり、Developer ID、notarization、Mac App Storeを要件にしません。以前のrun `33259524623`は旧release probeに対するcomponent証拠であり、現行semantic E2Eの合格証拠へ転用しません。現行candidateでは実Quick Action install、`/usr/bin/automator`実行、GitHub固定commitのメモリ内検証、contract、Codex/Claude delivery境界、uninstall、ローカルskill残留ゼロをCIで再試験します。Finderのメニュー表示そのものは[`macos-finder-friend-acceptance.md`](macos-finder-friend-acceptance.md)による友人の実Mac受入を必須とし、CIで代替しません。

0.3.3 candidateのrun [`33306460511`](https://github.com/omusubiman5/skill-magnet/actions/runs/33306460511)は過去設計の参考記録であり、0.4.0のrelease証拠には使用しない。

terminal cleanup hardeningを含む0.3.6 candidateのrun [`33307964947`](https://github.com/omusubiman5/skill-magnet/actions/runs/33307964947)も過去版の証拠である。0.4.0ではno-API handoff契約、129 test、standalone wheel、macOS Finder lifecycle、Windows certificate/native/MSIX lifecycleを改めて実行する。

0.4.0 candidateのrun [`33310087151`](https://github.com/omusubiman5/skill-magnet/actions/runs/33310087151)はWindows/macOSともgreenである。両OSで129 testとstandalone wheel payload gateがPASSした。macOSでは実Quick Action install、Automator実行、Codex/Claude handoff意味論、uninstall、残留ゼロを確認した。Windowsではcertificate state、installed wheelからのnative build、MSIX install/update/rollback/uninstall lifecycleを確認した。

0.4.1 path修正版のrun [`33311945165`](https://github.com/omusubiman5/skill-magnet/actions/runs/33311945165)もWindows/macOSともgreenである。両OS130 test、standalone wheel payload gate、macOS Finder lifecycle、Windows certificate/native/MSIX lifecycleがPASSした。
