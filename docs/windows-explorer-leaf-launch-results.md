# Windows Explorer package leaf release evidence

## 文書の位置づけ

この文書は現行のExplorer統合だけを記録する正本です。現行仕様は、packを`Skill Pack: <表示名>`、単体登録したskillを`Skill: <表示名>`として表示し、固定の`このフォルダーのスキルを登録`と`Library Manager`を加えます。pack leafは新規Codex Desktopタスク内でpackのINDEXと全skillを読み、依頼に必要な最小集合を選びます。skill leafは選択された1 skillだけを固定して渡します。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "release_scope": "configured-selection-leaves",
  "release_version": "0.5.8",
  "distribution_scope": "local-self-signed",
  "full_test_count": 187,
  "menu_leaf_count": 3,
  "menu_action_count": 5,
  "library_manager_entry_count": 1,
  "register_folder_entry_count": 1,
  "selection_kinds": ["package", "skill"],
  "pack_skill_counts": [1, 9, 12],
  "release_code_sha": "6d1e2b26662f15512ac41181628fba9b954efb2d",
  "wheel_payload_sha256": "fc59227fcf42ade3af3d10abe0162eef3dbce7e66b36f2f69edc0c43ef15d328",
  "automated_status": "LOCAL_RELEASE_GATE_PASS_187",
  "windows_explorer_field_status": "PASS_REAL_RIGHT_CLICK_MENU_AND_CONFIRMATION_UI_0_5_1",
  "macos_finder_field_status": "CI_SEMANTIC_ONLY_REAL_UI_NOT_CLAIMED_FOR_0_5_2",
  "public_distribution_status": "NOT_CLAIMED_REQUIRES_EXTERNAL_PUBLISHER",
  "codex_desktop_result_status": "HANDOFF_READY_ANSWER_COMPLETION_NOT_CLAIMED"
}
explorer-results-ledger:end -->

- 統合テスト: `python -m unittest discover -s tests -v` — 187 tests PASS、環境依存1件skip
- runtime skill folderを右クリックした際のworkspaceエラー経路そのものを廃止した。選択は正当なskill指定として保持し、task workspaceだけを`None`へ正規化して、利用者の再選択なしでprojectless新規タスクへ自動handoffする。最新build情報はリリース時のledgerを正とする。
- task workspaceをruntime skill rootから分離した。`~/.codex/skills`、`~/.agents/skills`、`~/.claude/skills`と配下を右クリックした場合は拒否せず、projectless新規タスクへ自動変換する。通常フォルダーは`作業対象フォルダー`として渡すが、runtime skill rootをcontract、prompt、deep linkの作業場所には入れない。最新build情報はリリース時のledgerを正とする。
- Library Managerは右クリック受付後にwindowと処理名を先に表示し、同一stateの多重processをOS file lockで排他する。同一folderの二重投入、別folderの並行投入、holder強制終了後の再取得を回帰試験で確認した。独立した2 buildの論理payload SHA-256はともに`f046efc06554f6ca15fce18d8ec924c308f628c7988e6ca09fe6aeee0b1ae05d`、release codeは`03088d6bd96ecf6a10de19db616bc8d5dcd38452`である。
- 実Windows再導入スモークで、PowerShellがBOM付きで保存した正常な`certificate-state.json`をPythonが破損扱いする再登録阻害を検出した。現在stateとrollback履歴を`utf-8-sig`で読み、BOM付きfixtureで所有権復旧を回帰化した。独立した2 buildの論理payload SHA-256はともに`c7afd078594a48b2f4fbcdeab2f4b3717bb4f7b6485b2ae059072438867fc71d`、release codeは`20050a4eccdbd7215e3dfbf31be90c275452c561`である。
- 標準Skill登録validationから`trigger`／`boundary`固定語検査を撤去し、必須frontmatter `name`／`description`の構造検査へ訂正した。実`android-cli`を既存libraryの隔離コピーへ登録・再登録し、ユーザーSkill直下132候補／内部155 skillの独立登録を全件PASSで確認した。0.5.8候補wheelの論理payload SHA-256は`a75bd3c26b7842c6a8aea47be80384399bc68bd4fc2b403356eb60b92809ec46`、release codeは`3631483db0505293c34e7372f0dc76f14fb748fb`である。
- menu contract: active 2 package leaves + `CMA004` skill leaf / 選択フォルダー登録1 action / `Library Manager` 1 action / selection kinds `[package, skill]` / 選択単位別skill数 `[1, 9, 12]`
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
- 0.5.4ではLibrary Managerへpack→skill一覧とCRUDを追加し、管理対象ファイルだけの削除公開、依存削除防止、隔離候補rollback、同一repository旧packの有効設定除去、GitHub差分0件時の再同期を実装した。CI検証済みwheelの論理payload SHA-256は`ce651401d6c84ad174633f31f98d249bad89b13cfce0bc79893d71e034749f1e`である。release codeは`f54b865dbbcae2625756c814c641942db18cb70d`である。
- 0.5.5では選択フォルダー登録を独立した右クリックactionにし、選択パスをCLI／GUI／登録処理まで保持する。通常の`Library Manager`を開いただけでは登録しない。CI検証済みwheelの論理payload SHA-256は`22a78075ff5208939e601ebea241cd06760046191fa08163171365a8c2d7b187`、release codeは`7c4b60a878e1e008ddef89fa2bf97f69c1f96103`である。
- 0.5.6では固定英単語だけだった契約判定を修正し、日本語の「時に使う」「制約」「行わない」等を意味区分として受理する。実`cma-004`をユーザーlibraryの隔離コピーへ登録し、22 skillsの全体validationがPASSした。登録中の強制終了に備えて隔離候補と検証済みbackupからの次回起動復旧を実装した。release candidate wheelの論理payload SHA-256は`fbfe1f67087ef4c5544b691ee7bcddf91d21ee6a7b6db2e40f68affbc78c8b32`、release codeは`3be279210f724ec3270fe3c8e06e528dcdb9e808`である。
- 0.5.7では修正前に保存されたlaunch contractも、handoff、actual-request SHA-256、output schema、完了検証、利用者向け結果の全境界で同じcanonical実依頼を使う。これにより旧contractの`&#x20;`再露出を防ぎ、contract digestによる完全性検証は維持する。独立した2 buildの論理payload SHA-256はともに`3edb1a62bb6af10ec3d2906c6c6983dbc1afaa88bc0c1489f3820209d098507b`、release codeは`8ffd77c649a9cd87d0a326661243284485ce5c01`である。
- 0.5.8ではLibrary Managerの登録・更新・削除を起点に、検証、push、PR作成、repository設定に応じた自動または即時merge、merge commit再検証、本体設定更新、右クリックメニュー再登録までを一つの再開可能transactionで完了する。実`cma-004`をskill保管庫PR #4でmergeし、2 pack leavesと`Skill: CMA004 — AI NEWS Podcast Audio`、合計5 actionsとして反映した。178 testsはPASS（環境依存1件skip）、独立した2 buildの論理payload SHA-256はともに`5b12f4ffc835586adaadb759d89be9d34b17065143f3909542fb05134e6c0452`、release codeは`4a750ab95040a3b4af6615692d7406e50d285aae`である。
- Library Manager transaction `8dc76704a259400e9b0a2259612155ce`を完走し、skill保管庫PR #3をmerge、merge commitのmanifestを再検証して`codex-cli`と`conflict-clarity`を有効化した。当時のmenuは3 package leavesとLibrary Managerの計4 actionで、`menu_contract_matches_config: true`、`usable_installed_state: true`であった。
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
