---
artifact: implementation-report
version: "1.1"
created: 2026-09-02
source: docs/skill-library-management-requirements.md
plan: docs/skill-library-manager-implementation-plan-2026-09-02.md
status: implemented
---

# Skill Library Manager 実装報告

## 結論

`skill-library-management-requirements.md`のFR-1〜FR-22を、共通domain層、タブのない1画面GUI、右クリック入口、CLI、Git transaction、atomic activation、status/receipt、回帰試験として実装した。skill repositoryは特定skill名に結び付けず、既定名を`skill-magnet-skills`とした。

公開処理は、draft検証 → isolated clone → staged blob preview → 明示確認 → non-force push/PR → remote Git blob再取得とSHA-256照合 → 明示確認 → config/menu有効化、という二段階transactionになった。remote検証前は現在のconfigを変更せず、menu更新失敗時はconfigをbyte単位で直前版へ戻す。同じtransaction IDの再実行は既存commit、push、activation receiptを再利用し、重複操作を行わない。

## 実装内容

### 製品入口

- Windows Explorerでは右クリック`Skill Magnet` → `Library Manager`から直接開く。pack実行項目は`Skill Pack: <表示名>`、単体skill項目は`Skill: <表示名>`として種別を明示する。
- macOS Finderではクイックアクション`Skill Magnet`の共通画面内にある`Library Manager`から開く。
- GUIの作業用repositoryは製品state内で自動作成・再利用し、保存先、repository名、`Draft directory`を入力させない。右クリックしたfolderから単一skill、pack、pack collectionを自動判定する。画面を開くだけではpublishもactivateも実行しない。
- `python -m skill_magnet library ui`でコンパクトなSkill Library Managerを開く。
- 登録とGitHub公開をタブのない1画面へ統合した。標準構成の作成済みスキルfolderを右クリックした場合は自動importして登録欄を隠し、手動登録時だけ同じ画面上部へfolder欄を表示する。repository、catalog/INDEX、validation、preview、activationの独立画面は設けない。
- 公開操作は状態連動の1ボタンへ統合した。初期状態は`送信内容を確認する`だけを表示し、成功後に同じボタンを`GitHubへ送る`、`GitHubのマージを確認する`、`Skill Magnetへ反映`へ切り替える。Transaction未作成時に後続操作を押せる経路をなくした。
- OSは実行環境から自動判定し、利用者へ選択させない。構成不備、URL不備、validation失敗は操作時のエラーダイアログで停止する。
- 現在の設定にrepository URLが一意に存在する既存ユーザーには、そのURLをPublish画面へ自動表示する。複数候補は誤選択防止のため自動補完しない。
- 手動登録画面の入力はfolder 1つだけとし、単一skill、1 pack、複数pack collectionを判別する。pack／skill ID、表示名、目的、順序、関係はfolder、`SKILL.md`、`INDEX.md`から取得する。登録元に`acceptance.json`がなければ内部互換メタデータを生成し、`test-prompts.json`がある場合はそのSHA-256を記録する。
- `C:\Projects\cangjie-skill-clean\books`の実データで3 pack・33 skillを母集合として固定し、3 pack・33 skillすべての一括登録を確認した。`conflict-clarity`のroot `SKILL.md`もentry skillとして含め、子skillへの相対linkを配置先に合わせる。
- 登録済みsourceの再選択を冪等なno-opにした。pack ID、skill集合、保存directoryが完全一致すれば`登録済み`として成功し、catalogを変更しない。一部だけ一致する場合は破損を隠さず停止する。
- headless/運用用途として`init`、`add`、`validate`、`prepare`、`publish`、`verify-merged`、`activate`、`status`も提供する。
- publishとactivateは独立した明示確認が必要で、未確認ならfail-closedで停止する。

### Library contractと安全検証

- repository rootに`skill-magnet.catalog.json` schema v1を導入した。
- 一repository内の複数pack、複数skill、表示順、metadata、relationsをcatalogで管理する。
- packごとの元`INDEX.md`をcatalogへ保持し、複数packでは決定的な統合`INDEX.md`を生成する。元INDEXがないpackはcatalog relationsから生成する。
- skill／pack ID重複、INDEX参照先欠落、unsafe path、symlink、secret候補、SKILL.md不足、frontmatterの`name`不一致／`description`不足、acceptance assertion不正を拒否する。標準Skillへ独自の`Trigger`／`Boundary`見出しを強制しない。
- `depends-on`の未知skill・欠落依存・cycleを拒否する。`contrasts-with`は同一pack内の候補関係として保持し、実依頼での同時適用禁止をINDEX経由でLLMへ渡す。

### Publish transaction

- アプリ管理下の未公開libraryは公開処理の読取り入力に限定し、処理前後のtree digestが一致しなければ停止する。
- 製品state配下のtransaction固有workspaceへcloneし、そこだけを編集・commitする。
- Windowsの改行変換差を吸収するため、preview digestはworking treeではなくGit index blobから計算する。
- push後は別のremote-verifier cloneを作り、commitの全対象Git blobを再取得してpreview manifestと比較する。
- 既定は専用`codex/skill-library-<transaction>` branchとPR。direct publishはdefault branchをprepareし、`--direct --no-pr --confirm`を同時に明示した場合だけ許可する。
- credentialを含むremote URL、query、fragmentを拒否し、tokenやcommand出力をjournal/receiptへ保存しない。

### Activation、rollback、status

- remote検証済みcommitから本体configのpack、skill順、metadata、完全commit SHAを生成する。
- candidate configを既存`Config` parserで再検証後、atomic replaceする。
- menu shapeが変わった場合だけ既存Windows Explorer/macOS Finder transactional installerを呼ぶ。
- menu失敗時は旧config bytesを復元し、transactionを`published_but_inactive`へ戻す。
- statusは`draft`、`unpublished_edit`、`published_pending`、`published_but_inactive`、`active`を区別し、remote HEAD、verified commit、active commit、pack/skill集合、Windows/macOS共通contractを表示する。
- receiptはrepository、commit、changed files、manifest、pack/skill、config digest、menu結果、test結果、transaction IDを保存する。
- activation完了後はauthoring workspaceとremote verifierを削除する。永続するのはjournal/receipt metadataだけである。

## 要件トレーサビリティ

| Requirement | 実装 | 検証 |
|---|---|---|
| FR-1 | 汎用既定名`skill-magnet-skills`、skill/pack名から非生成 | naming round-trip test |
| FR-2 | catalogの複数pack/skill model | add/publish E2E |
| FR-3 | machine-readable catalog schema v1 | catalog validation/manifest test |
| FR-4 | 既存directory接続、既存名維持、remote default branch読取 | GUI connect、prepare E2E |
| FR-5 | folder 1つからskill／pack／collectionを判別し、候補母集合を一括import | collection completeness test、実`books` smoke |
| FR-6 | path/symlink/secret、必須frontmatter、acceptance検証、不在acceptance生成。固定契約見出しは非強制 | negative tests、標準Skill互換test、generated metadata test |
| FR-7 | pack ID指定で既存追加／新規作成 | add round-trip test |
| FR-8 | 3 relation、unknown/cycle検証、contrast共存 | relation parse/negative tests |
| FR-9 | staged blob manifestを含むpreviewと確認gate | publish confirmation test |
| FR-10 | transaction固有clone、draft digest不変 | isolated publish E2E |
| FR-11 | branch/PR既定、directの複合明示gate、non-force push | publish E2E/guard test |
| FR-12 | 別cloneからcommit blobを再取得し全digest比較 | remote verification E2E |
| FR-13 | verified以外のactivation拒否 | state gate test |
| FR-14 | catalogからconfig packを生成しatomic replace | activation E2E |
| FR-15 | menu shape digestで条件実行 | menu callback assertion |
| FR-16 | Windows/macOSへ同一config contractをstatus表示 | platform parity assertion |
| FR-17 | sanitized receipt schema | receipt E2E |
| FR-18 | `published_but_inactive`状態 | pre-activation status assertion |
| FR-19 | menu failure時の旧config byte復元 | failure injection test |
| FR-20 | remote/verified/active/edit/pending状態表示 | status assertions |
| FR-21 | transaction ID再利用、commit/push/receipt重複防止 | retry commit-count test |
| FR-22 | Explorer固定action、Finder共通画面button、選択folder prefill | manifest/registry/path/CLI callback tests |

## Policy整合

従来policyの「local skill content全面禁止」は、新要件のisolated authoring workspaceと矛盾していた。永続的な正本をGitHubだけに保つ安全境界は維持しつつ、利用者が明示したlibrary編集transaction中だけ製品所有workspaceへの一時複製を許可し、runtime materialization禁止と完了後cleanupをpolicy、README、policy testへ同時反映した。OpenAI/Anthropic API key、従量課金API、追加支払いは導入していない。

## 変更ファイル

| File | 内容 |
|---|---|
| `src/skill_magnet/library_manager.py` | catalog、validation、publish transaction、activation、status、receipt |
| `src/skill_magnet/library_ui.py` | タブのない1画面GUI、OS自動判定 |
| `src/skill_magnet/cli.py` | `library` command群とGUI入口 |
| `src/skill_magnet/platforms.py` | Explorer/Finder右クリックmanager actionと状態契約 |
| `src/skill_magnet/ui.py` | Finder共通画面からmanager GUIへの遷移 |
| `native/windows-modern-context-menu/*` | Windows 11 modern menuのmanager action |
| `tests/test_library_manager.py` | 正常系、negative、retry、rollback、CLI/GUI contract |
| `policy/product-policy.json` | isolated authoring transaction境界 |
| `tests/test_product_policy.py` | policy境界検証 |
| `README.md` | GUI/CLI利用手順と安全境界 |
| `docs/mvp-redesign.md` | canonical policy mirror |
| `docs/windows-explorer-leaf-launch-results.md` | 現行test countとmanager actionへ同期 |

## 検証結果

| Command / check | Result |
|---|---|
| `python -m compileall -q src tests` | PASS |
| `python -m unittest tests.test_library_manager tests.test_product_policy tests.test_results_gate -q` | PASS |
| `python -m unittest discover -s tests` | 157 PASS、1 environment-dependent skip |
| `python -m pip wheel . --no-deps` | PASS、`library_manager.py`と`library_ui.py`のwheel収録を確認 |
| `git diff --check` | PASS |
| 実GUIスモーク | PASS、登録と公開を同じ画面に表示し、標準folder選択時は登録欄を非表示 |

自動試験ではローカルbare Git remoteを用い、isolated clone、commit、direct push、別cloneからのremote blob検証、config activation、retry、menu failure rollbackまで実行した。GitHub PR経路は`gh pr create`／`gh pr view`へ接続済みであり、実repositoryでは既存GitHub credentialとbranch protectionが最終権限境界になる。

実GUIのスモーク結果は[Skill Library Managerスモークテスト結果](skill-library-manager-smoke-test-2026-09-02.md)へ記録した。

## 完了判定

実装、要件対応試験、全回帰試験、利用手順、policy整合、実装報告まで完了した。公開先repositoryの実credentialを使う変更や実OS menuの再登録は、利用者がGUIまたはCLIで対象repositoryと明示確認を選ぶ製品操作であり、本実装作業中には自動実行していない。
