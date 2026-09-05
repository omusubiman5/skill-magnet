---
artifact: prd
version: "1.7"
created: 2026-09-02
status: implemented
---

# 要件定義: Library Manager

## Overview

### Problem Statement

Skill Magnetは本体repositoryとskill repositoryを分離し、固定commitから安全にskillを読む設計になっている。一方、skill追加時にはrepository編集、INDEX更新、commit、検証、pack設定更新を利用者が手作業で行う必要がある。この手順は製品の中核操作なのにアプリ外へ漏れており、digest、固定commit、pack構成の更新漏れを起こしやすい。

また、skill repositoryの名称が特定skillまたは特定packを表すように見えると、一つのrepositoryへ複数領域のskillを継続追加できる「ライブラリ」という役割が伝わらない。repository名は特定skill／packから独立させる必要がある。

### Solution Summary

Skill Magnetへ「Library Manager」を追加する。利用者はアプリ内で汎用skill repositoryを接続または作成し、新規skillの追加、packへの所属、INDEX関係編集、検証、Git commit／push、固定commit確定、本体lock更新までを一つの案内付きtransactionとして完了できる。永続的なskill正本はユーザー所有GitHub repositoryだけとし、Codex／Claudeのskill directoryへインストールしない。0.5.9のOS入口は子のない単一rootで、pack／skill変更時も再登録せず、起動後の統合GUIが現行configを読む。

Skill Magnetの実行ターゲットはCodex DesktopアプリとClaude Codeデスクトップアプリである。Library Managerが有効化したpackは、この二つのデスクトップアプリへhandoffできる状態にする。

推奨repository名は`skill-magnet-skills`とする。名称は利用者が変更できるが、最初のskill ID、pack ID、特定AI名から自動生成しない。既存の`codex-pmo-skills`は接続可能な既存repositoryとして扱い、自動renameしない。

### Target Users

- 自分のskillを継続的に追加・整理するSkill Magnet利用者
- 複数skillをpackとして配布・保守するskill library管理者
- 固定commitと検証証拠を維持したまま更新したいリポジトリ管理者

## Goals & Success Metrics

### Goals

1. skill追加と製品反映をアプリ内の一つのguided flowで完結させる。
2. repository、pack、skillの名称と責務を分離する。
3. 固定commit、digest、INDEX関係、pack構成を機械検証し、壊れた更新を有効化しない。
4. GitHub書込みとローカル設定更新を安全に分離し、途中失敗時も現在の有効版を維持する。

### Success Metrics

| Metric | Current Baseline | Target | Timeline |
|---|---:|---:|---|
| skill追加に必要な手動repository/config操作 | 7操作以上 | 0操作 | GA |
| アプリ内で完了できる新規skill公開フロー | 0% | 100% | GA |
| 有効化されたpackの固定commit・digest整合率 | 手動確認 | 100% | 全build |
| 途中失敗後に旧有効版を保持する割合 | 未定義 | 100% | 全failure test |
| skill追加から有効化までの操作時間 | 未計測 | 5分以内（レビュー・CI待ちを除く） | Beta測定 |
| menu/configの更新漏れによる実行失敗 | 現状測定なし | 0件 | GA後30日 |

### Non-Goals

- Skill Magnet本体repositoryへskill本文を恒久保存すること
- OpenAI／Anthropic APIでskill本文を自動生成すること
- GitHub以外のskill保管backendを初期releaseで提供すること
- 利用者の確認なしにdefault branchへ直接pushすること
- repository名を特定skill名やpack名へ強制すること

## User Stories

| ID | User Story | Priority |
|---|---|---|
| US-1 | 利用者として、汎用名のskill repositoryをアプリから作成または接続し、複数skillを蓄積したい | P0 |
| US-2 | skill作者として、標準構成のSKILL.mdを追加し、必須frontmatter不足を公開前に発見したい | P0 |
| US-3 | pack管理者として、skill所属とdepends-on／composes-with／contrasts-withを画面で編集したい | P0 |
| US-4 | 利用者として、commit、digest計算、本体反映をアプリへ任せ、単一rootの統合GUIで更新後の選択肢を使いたい | P0 |
| US-5 | repository管理者として、push前に全差分と書込み先を確認し、PR経由で公開したい | P0 |
| US-6 | 利用者として、途中失敗しても現在利用中のpackを壊したくない | P0 |
| US-7 | 保守者として、remote更新とローカル有効版の差分をアプリで確認したい | P1 |
| US-8 | 利用者として、登録済みpack／skillを一覧し、内部IDを入力せず更新・削除したい | P0 |

## Scope

### In Scope

- 汎用skill repositoryの作成・接続・状態確認
- 新規skillの作成または既存directoryのimport
- SKILL.md、acceptance.json、INDEX関係、pack catalogの編集と検証
- isolated workspaceでのGit branch、commit、push、PR作成
- full commit SHAと各対象file SHA-256の算出・照合
- 検証済みcommitを参照する本体lock/configの自動更新
- 検証済みconfig更新後、再登録なしで単一rootの統合GUIへpack／skillを反映
- 中断復旧、rollback、監査receipt

### Out of Scope

- skill本文のLLM自動生成
- GitHub repositoryの自動rename
- GitHub以外のGit hosting
- remote default branchの保護rule変更
- Skill Magnet本体とskill repositoryを一つへ統合すること
- AI実行結果の品質評価

### Future Considerations

- 複数skill repositoryの横断検索
- organization共有libraryと承認workflow
- skillのsemantic versionとdeprecated migration
- GitHub以外のprovider adapter

## Solution Design

### Repository Identity and Catalog

- FR-1: アプリはskill repository名をskill IDまたはpack IDから生成してはならず、default候補として`skill-magnet-skills`を提示しなければならない。
- FR-2: 一つのskill repositoryは複数skillと複数packを保持できなければならない。
- FR-3: アプリはrepository内のmachine-readable pack catalogを管理し、pack ID、表示名、目的、skill順序、skill metadataを保持しなければならない。
- FR-4: 既存repositoryを接続する場合、owner、remote URL、default branch、既存skill、INDEX、catalogを読取り、renameせずに利用できなければならない。

### Skill Authoring and Validation

- FR-5: 利用者はfolderを1つ指定し、単一skill、1 pack、または複数pack collectionを登録できなければならない。アプリは直下構造、`SKILL.md`、`INDEX.md`からpack／skillの母集合、ID、表示名、目的、順序、関係、root entry skillを自動取得しなければならない。全候補を登録または理由付き拒否へ分類し、一部だけを黙って登録してはならない。同一内容の登録済みsourceを再選択した場合は成功するno-opとし、重複エラーや再書込みを行ってはならない。同じID／所属でも`SKILL.md`その他の管理対象byteが異なれば更新として扱い、既存pack更新と新pack追加が混在するcollectionも一つのatomic操作にしなければならない。catalogと保存ファイルが部分的にしか一致しない場合はfail-closedで停止しなければならない。
- FR-6: 公開前検証はskill／pack ID重複、INDEX参照先欠落、path traversal、symlink、secret候補、`SKILL.md`、必須frontmatterの`name`／`description`、acceptance schemaをfail-closedで検査しなければならない。`trigger`／`boundary`という文字列や特定の日本語見出しは標準Skillの必須構造ではないため、固定語の有無で登録を拒否してはならない。登録元の`acceptance.json`は任意とし、不在時は内部互換メタデータを生成する。`test-prompts.json`があれば、そのSHA-256を生成物へ結び付けなければならない。
- FR-7: アプリは指定されたフォルダー構成から単一skill、1 pack、複数pack collectionを自動判定し、既存IDとの一致は更新、未知IDは新規登録として扱わなければならない。利用者へpack ID入力や「既存pack／新規pack」の選択を要求してはならない。
- FR-8: アプリはINDEXの`depends-on`、`composes-with`、`contrasts-with`を取込み・検証し、未知skill、自己参照、dependency cycleを拒否しなければならない。`contrasts-with`のskillは同じpackへ共存できるが、実依頼への適用時に同時採用してはならない。
- FR-26: アプリはcatalogからpack→skillの階層、表示名、説明、所属を一覧表示しなければならない。内部IDは照合用に表示できるが、CRUD操作の入力値として利用者に要求してはならない。
- FR-27: 選択したskillまたはpackを同じIDのフォルダーから更新できなければならない。ID不一致、構成不正、検証失敗時はcatalog、INDEX、skillファイルを変更前へ戻さなければならない。
- FR-28: 選択したskillまたはpackを削除できなければならない。依存されているskill、最後のpack／skillは拒否し、pack削除では他packと共有されていないskillファイルだけを削除しなければならない。
- FR-29: 別pack IDであってもskill集合が同一なら重複packとして新規登録を拒否しなければならない。remote検証後の有効化では同じrepository URLの旧pack集合をcatalogの現行集合で置換し、削除済みpackをメニューへ残してはならない。

### Publish Transaction

- FR-9: 登録・更新・削除の利用者操作を、その変更に限ったGitHub公開、自動マージ、remote検証、本体更新の明示承認として扱う。アプリは実行結果と対象repository、branch、変更file、pack、commit、PR、反映結果を一画面へ表示し、追加の段階ボタンを要求してはならない。validation失敗時はpublish前に停止する。
- FR-10: 編集作業は利用者の既存checkoutではなく、製品所有のisolated temporary workspaceで行い、未コミット作業を変更してはならない。
- FR-11: defaultでは専用branchへcommit・pushしてPRを作成し、default branchへの直接pushは明示的に選択され、かつrepository policyが許可する場合だけ実行できなければならない。
- FR-12: remote commit確定後、アプリは40文字commit SHA、INDEX、全対象SKILL.md、acceptance.json、catalogのSHA-256をremote contentから再取得して照合しなければならない。
- FR-13: remote検証が完了するまで、現在有効な本体lock/configを変更してはならない。

### Activation and Direct-root UI Update

- FR-14: 検証済みremote commitから、repository URL、固定commit、pack catalog、skill順序、metadata、digestを本体の生成lockへ自動反映しなければならない。
- FR-15: skill本文、pack追加・削除、表示名、skill所属、commit、その他config内容の変更ではOSメニューを再登録してはならない。子のない単一rootが起動時に現行configを読み、統合GUIの選択肢を更新しなければならない。再登録はinstalled executable／config location、native adapter contract、package／Quick Action integrationを明示的に修復・更新する別操作だけに限定する。
- FR-16: WindowsとmacOSで同じcommit、pack、skill集合が有効になったことをstatus画面で確認できなければならない。
- FR-17: 更新完了時にrepository、commit、changed files、digest、pack差分、config結果、menu結果、test結果を含むreceiptを表示・保存しなければならない。

### Failure, Recovery, and Status

- FR-18: GitHub書込み成功後に本体更新が失敗した場合、新commitは「published but inactive」と表示し、旧commitを有効なまま維持しなければならない。
- FR-19: 本体lock更新が途中失敗した場合は直前のlockを復元し、部分成功を完了表示してはならない。別の明示的OS入口repairでrollbackする場合は、snapshotの所有path、metadata、hash、package identity、file manifestを破壊的操作より前に完全検証し、不一致なら現行状態を変更せず停止しなければならない。
- FR-20: アプリはremote HEAD、検証済みcommit、現在有効なcommit、未公開編集、published-but-inactive更新を区別して表示しなければならない。
- FR-21: retryは同じtransaction IDを使用し、同じcommitを重複作成・重複push・重複登録してはならない。
- FR-23: どの処理段階で中断してもjournalから同じtransactionを再開できなければならない。CRUDのatomic置換前にlocal mutation ID、remote baseline、結果manifest、操作履歴を永続化し、CRUD完了後からpublish transaction作成前にprocessが強制終了しても次回GUIからGitHub反映または明示破棄へ進めなければならない。remote副作用がないことを確認できる段階だけ、GUIとCLIで「ローカル作業を破棄」を許可する。commit／push／PRが存在する、または存在が不明な段階ではlocal-only破棄を禁止し、remote状態を照合して既存branch／PRを再利用する。公開は管理対象ファイルのoverlayに限定し、既存remoteファイルの削除差分をfail-closedで拒否しなければならない。
- FR-24: PRのOPENは正常な`waiting_for_merge`であり、例外、処理中断、復旧対象として表示してはならない。CLOSED未merge、MERGED、未知状態、merge後digest不一致を別状態として扱う。差分0件ではcommit、push、PRを作成してはならない。
- FR-25: 同一libraryとremoteに非終端transactionがある場合、新transactionを作らず最新の対象を再開しなければならない。操作中は実行ボタンを無効化し、二重clickで段階を跨いだ操作を実行してはならない。
- FR-30: 右クリック起動では、対象folder、設定、保存済みjournal、管理workspaceの検査より先に画面と受付状態を表示し、それらのI/OとCRUDをTk main thread外で実行して現在の処理名を継続表示しなければならない。Library Managerは同一stateにつき1 processだけ実行可能とする。統合画面からLibrary Managerへ移る際は同じ実行lockのphaseとwindow handleを原子的に引き継ぎ、同一folderの連続投入は生存中のManagerを前面化して重複処理せず、別folderの並行投入は既存処理を保持して理由と再試行方法を表示する。実行lockはprocess異常終了時にOSが解放し、lock fileの残存だけを理由に次回起動を拒否してはならない。
- FR-31: Windowsのinstalled stateはAppx version、同じmenu executableでimportしたmodule version／絶対path、distribution metadata／所有path、runtime tree digest、release wheel／source digestを一つのbuildへ拘束しなければならない。いずれかが異なるsplit-generationは、package登録が正常でも実機受入とfield evidenceを拒否しなければならない。
- FR-32: Windows native buildは固定入力集合のsource tree digestをmanifestとDLL export／埋込markerへ拘束し、signed MSIX、登録済みpackage root、外部install rootのmanifest／DLL／identity payload一致を検証しなければならない。rollbackは所有path、完全metadata schema、registry hash、package identity、外部file manifestをuninstall／削除より前に検証し、不一致時は現行状態を変更してはならない。
- FR-33: Windowsではclassic context menuを新規生成・登録する経路を提供してはならない。`render-context-menu --platform windows`とclassic登録APIは、registry内容の出力や`reg add`より前にfail-closedで拒否する。旧版classic／legacy rootの検出、backup、rollback、uninstall、modern導入時の削除だけは移行・復旧互換として保持する。

### User Experience

- FR-22: Library ManagerはOSの右クリック`Skill Magnet`入口から開けなければならない。Windows Explorerでは子メニューを持たない単一の`Skill Magnet`を押すと共通画面が直接開き、macOS Finderでは`Skill Magnet`クイックアクションが同じ共通画面を開く。共通画面には`Library Manager`、`このフォルダーのスキルを登録`、`Skill Pack: <表示名>`（単体skill選択を構成する場合は`Skill: <表示名>`）を表示し、管理機能・pack・skillの種別を見ただけで区別できなければならない。作業用repositoryはアプリ専用state内で自動管理し、利用者へ保存先やrepository名を入力させない。登録操作では右クリック対象から、単一スキル、1パック、または複数パックを含む親フォルダーのいずれかを自動判定する。各スキルの`SKILL.md`が不足する場合はGitHubへ送る前に具体的な不足パスを表示して停止し、正常ならFR-9の自動公開・反映transactionを完了する。

基本flowはタブのない1画面とする。作業用repository、catalog、INDEX、validation、preview、activationのためだけの独立画面は設けず、自動処理または同じ画面へ統合する。

1. 右クリック対象が単一skill、1 pack、または複数pack collectionの標準構成なら、全候補を自動importして登録欄を隠す。作成済みskill／packを手動登録する場合だけ同じ画面の上部へfolder指定欄を表示する。画面内でskillを新規作成してはならない。pack情報からcatalogと統合INDEXを自動生成する。
2. 既存GitHub URLを自動取得し、validation後に専用branch、PR、自動マージ、merge後remote照合、activationを一つのtransactionで完了し、active versionとreceiptを表示する。OSメニューは再登録せず、次回root起動時に現行configを読む。

OSは利用者へ選択させず実行環境から自動判定する。URL未入力、標準構成不備、validation失敗はその操作時のエラーとして表示し、外部書込みまたはactivationを行わない。PRのOPENはエラーではなく正常なマージ待ちとして表示する。
手動登録画面で利用者が指定するのはfolderだけとする。単一skillでは内部の`Custom skills`へ登録し、統合GUIの選択肢に`Skill: <skill表示名>`として表示する。Explorer/Finder側へskill／packの子項目は作らない。pack folderではfolder名をPack IDにして直下の全skillを登録し、collection folderでは直下の全packを一括登録する。`SKILL.md`が一件もない、INDEX参照先がない、IDが衝突するなど完全性を証明できない場合は、全体をrollbackしてエラー停止する。
現在の有効設定にGitHub repository URLが一意に存在する場合はPublish画面へ自動表示する。候補が複数あり一意に決められない場合だけ空欄とし、誤ったrepositoryを自動選択しない。

画面ではrepository、pack、skillを別の概念として表示する。repository名をskill名として表示したり、pack名をrepository名として補完したりしない。

### Edge Cases

| Scenario | Expected Behavior |
|---|---|
| 同じskill IDが既に存在する | 新規追加を拒否し、編集flowへの切替を提示する |
| INDEXに未知skillがある | publish不可。該当relationを表示する |
| depends-on cycleがある | publish不可。cycle pathを表示する |
| contrasts-withの両方が同じ必須集合に入る | packを有効化せず、関係修正を要求する |
| push後にremote branchが更新された | force pushせず、再取得・再base・再previewする |
| PRが未merge | commitをpublished-pendingとして表示し、有効版は変更しない |
| PRがOPEN | 正常なマージ待ちとしてPRを開く導線を表示し、破棄／復旧ダイアログを出さない |
| PRがCLOSED未merge | 再openまたは状態保持を案内し、新PRを自動作成しない |
| 同じlibrary／remoteの操作を再開 | 既存transaction、branch、PRを再利用する |
| 同じfolderを処理中に再度右クリック | 既存処理を維持し、二重登録・二重transaction・二重PRを作らない |
| 別folderを処理中に右クリック | 並行処理を開始せず、実行中であることと完了後の再試行を表示する |
| Library Managerが強制終了 | OS lock解放後の次回起動を許可し、journalがあれば既存transactionを再開する |
| 差分0件 | GitHubへ送信せず「変更なし」で完了する |
| GitHub接続が切れた | local temporary編集を安全に保持し、外部成功を主張しない |
| config更新に失敗した | configを直前版へrollbackし、単一rootは変更しない |
| 既存repository名がdomain固有 | そのまま接続可能。renameを強制・自動実行しない |
| secret候補を含む | commit前に拒否し、該当fileとruleだけを表示する |

## Technical Considerations

### Constraints

- skill contentの唯一の永続的source of truthはユーザー所有GitHub repositoryとする。
- authoring用contentはisolated temporary workspaceに限り、完了・取消・失敗cleanup後に残さない。
- Manager起動時は設定済みremoteの検証済みcommitまたは取得したHEADからauthoring workspaceをhydrateする。skill本文は未完了transaction中だけ復旧用に保持し、成功・変更なし・送信前の明示破棄後は削除する。削除可能性はstate側とworkspace内側に同じnonceを持つ双方向の所有証明、全transaction journal、local mutation journalを共通CRUD lock内で再確認する。旧版の未標識workspaceはremoteや内容が一致しても手動cloneと区別できないため自動所有化・自動編集・自動削除せず、明示的なGitHub復旧時も元フォルダーを別名backupとして保持する。破損journal、symlink、junction、所有nonce不一致があればfail-closedで保持する。
- `~/.codex/skills`、`~/.agents/skills`、`~/.claude/skills`自身および解決後の子孫をLibrary Managerのstate directoryにしてはならない。
- activationは完全なcommit SHAへ固定し、branch名またはHEADを直接参照しない。
- SHA-256はローカル編集物ではなく、push後にremoteから再取得したbytesを基準にする。
- GitHub credentialをproject file、config、receipt、logへ保存しない。
- OpenAI／Anthropic API key、従量課金API、追加支払いを要求しない。
- Windows ExplorerとmacOS Finderの既存安全境界を維持する。

### Integration Points

- GitHub: repository作成・読取、branch push、PR作成、remote commit/content検証
- Git: isolated workspace、diff、commit、non-force push
- Skill Magnet config/lock: 検証済みcommitとpack catalogのactivation
- Explorer/Finder adapter: 子のない単一root／Quick Actionと、明示repair時だけの破壊前検証付きtransactional reinstall
- Existing release gate: pack count、skill count、version、wheel payload、provenance検証

### Data Requirements

- 永続化可能: repository URL、owner、固定commit、file digest、pack catalog、transaction status、sanitized receipt
- 永続化禁止: GitHub token、skill authoring temporary checkout、認証応答、未mask secret本文
- receiptは秘密情報を含まず、同一transactionを追跡できるIDを持つ。

## Agent Execution Contract

### Authoritative Sources

| Source | Path or link | Definitive for |
|---|---|---|
| 本要件定義 | `docs/skill-library-management-requirements.md` | 機能scope、FR、完了条件 |
| Skill repository契約 | `docs/skill-repository-contract.md` | repository、固定commit、skill file契約 |
| 製品設定 | `skill-magnet.json` | 現行packとactivation入力 |
| Product policy | `policy/product-policy.json` | GitHub source of truth、no-API、明示確認境界 |
| Platform implementation | `src/skill_magnet/platforms.py` | Explorer/Finder登録とrollback境界 |

矛盾時は、本要件定義の新しいlibrary management要件、product policyの安全境界、現行実装詳細の順に優先する。安全境界を緩和する解釈は禁止する。

### Do Not Touch

| Path or system | Reason it is off limits |
|---|---|
| 利用者の既存skill repository checkout | 未コミット作業を上書きしないため |
| GitHub default branch protection | 製品がrepository governanceを変更してはならないため |
| global/user Codex・Claude設定 | skill管理機能のscope外で認証・実行環境を壊すため |
| `v0.5.1`、`v0.5.2`既存tag | 公開済み固定履歴を改変しないため |
| Skill Magnet以外が所有するExplorer/Finder項目 | 他製品の状態を変更しないため |

### Requirement Verification Map

| Requirement | How it is verified | Who verifies |
|---|---|---|
| FR-1 | 初期repository名とskill/pack名非連動のUI test | Automated + QA |
| FR-2 | 一repositoryへ複数pack・複数skillを追加するE2E | Automated |
| FR-3 | catalog round-tripとschema negative test | Automated |
| FR-4 | domain固有名の既存repository接続test | Automated |
| FR-5 | create/import双方のUI・file contract test | Automated + QA |
| FR-6 | duplicate、traversal、symlink、secret、schema各negative test | Automated |
| FR-7 | 既存pack追加と新規pack作成E2E | Automated |
| FR-8 | relation正常系、未知skill、cycle、contrast negative test | Automated |
| FR-9 | push直前preview内容と明示確認test | Automated + QA |
| FR-10 | dirty既存checkoutの前後hash不変test | Automated |
| FR-11 | branch/PR defaultとdirect-push拒否test | Automated |
| FR-12 | remote bytes改ざん、SHA不一致、commit drift test | Automated |
| FR-13 | remote検証失敗時のactive lock不変test | Automated |
| FR-14 | catalogから生成lockへの完全mapping test | Automated |
| FR-15 | pack／skill／commit／config変更時no reinstall、単一root起動時config再読込test | Automated |
| FR-16 | Windows/macOS statusのcommit・skill集合一致CI | Automated |
| FR-17 | receipt schema、secret非混入、全証拠参照test | Automated |
| FR-18 | push後activation失敗のpublished-but-inactive E2E | Automated |
| FR-19 | config rollbackと、明示menu repair snapshot改ざん時の破壊前fail-closed E2E | Automated |
| FR-20 | 5状態のstatus表示snapshot test | Automated + QA |
| FR-21 | 同一transaction retryのcommit・push・menu重複ゼロtest | Automated |
| FR-22 | Explorer/Finder右クリック導線、選択folder prefill、no-auto-write test | Automated + Windows QA |
| FR-30 | window先行表示、同一／別folder競合、強制終了後lock再取得test | Automated + Windows QA |
| FR-31 | Appx／module／distribution／runtime／wheel-sourceのsplit-generation拒否test | Automated + Windows QA |
| FR-32 | native manifest／DLL marker／MSIX改ざん拒否とrollback破壊前不変test | Automated + Windows QA |

### Stop and Escalate

| Condition | Escalate to |
|---|---|
| repository renameまたは移転が必要 | Repository owner |
| default branchへ直接pushする必要がある | Repository owner |
| branch protection、required review、CI ruleと衝突する | Repository owner / maintainer |
| secret候補が正当なfixtureか判断できない | Skill author |
| product policyと新しいcatalog formatが矛盾する | Product owner |
| rollbackで旧active versionを証明できない | Release owner |

## Dependencies & Risks

### Dependencies

| Dependency | Owner | Status | Impact if Delayed |
|---|---|---|---|
| GitHub認証とrepository write権限 | User / GitHub | Required | publish不可、アプリ内の未公開データのみ |
| machine-readable pack catalog format | Engineering | 未実装 | 自動config生成不可 |
| isolated Git transaction engine | Engineering | 一部既存 | 安全なpublish不可 |
| Explorer/Finder direct-root integration | Engineering | 既存 | 統合GUIへの正規入口を提供不可 |
| 実Windows/macOS UX受入 | QA / Product owner | Releaseごと | GA判定不可 |

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| cross-repository更新が途中で分断される | M | H | remote publishとactivationを二段階化し、旧lockを維持 |
| GitHub tokenがlogへ漏れる | L | H | OS credential storage、redaction、secret scan |
| INDEX自動編集が意味を壊す | M | H | relation UI、preview、cycle/contrast validation |
| repository名変更で既存URLが壊れる | M | M | 自動rename禁止、明示migrationのみ |
| pack／skill変更で不要なmenu再登録が走る | M | M | Library transactionから再登録を除外し、明示repairだけに限定 |
| temporary workspaceが残る | L | M | product-owned path、journal、startup recovery、residue test |

## Timeline & Milestones

| Milestone | Description | Target Date |
|---|---|---|
| M1: Contract | catalog schema、transaction state、receipt schemaを確定 | 実装開始前 |
| M2: Local Authoring | create/import、validation、pack/INDEX編集、preview | Sprint 1 |
| M3: GitHub Publish | isolated commit、push、PR、remote digest検証 | Sprint 2 |
| M4: Activation | generated lock、単一rootからのconfig再読込、rollback、status | Sprint 3 |
| M5: Cross-platform Beta | Windows/macOS E2Eと実機UX受入 | Sprint 4 |
| GA | 全FR検証、migration文書、release gate合格 | Beta合格後 |

## Open Questions

- [ ] 既存`codex-pmo-skills`を将来`skill-magnet-skills`へrenameするか。自動renameはしない。Owner: Repository owner
- [ ] GitHub認証を既存`gh` credential優先にするか、OAuth device flowも提供するか。Owner: Engineering / Security
- [ ] pack catalogのfilenameとschema versioningをADRで確定する。Owner: Engineering

## Appendix

### Related Documents

- [スキル保管庫契約](skill-repository-contract.md)
- [実装計画](skill-library-manager-implementation-plan-2026-09-02.md)
- [実装報告](skill-library-manager-implementation-report-2026-09-02.md)
- [スモークテスト結果](skill-library-manager-smoke-test-2026-09-02.md)
- [MVP再設計](mvp-redesign.md)
- [製品ポリシー](../policy/product-policy.json)
- [0.5.2リリース候補報告](release-0.5.2.md)

### Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-09-02 | Codex | 初版。汎用repository命名とアプリ内skill追加transactionを定義 |
| 1.4 | 2026-09-03 | Codex | 登録・更新・削除からGitHub公開、PR自動マージ、本体反映、メニュー再登録までの自動transactionを必須化 |
| 1.1 | 2026-09-02 | Codex | FR-1〜FR-21の実装・検証完了に伴いstatusをimplementedへ更新 |
| 1.2 | 2026-09-02 | Codex | 実行ターゲットをCodex Desktopアプリ／Claude Codeデスクトップアプリとして明記 |
| 1.3 | 2026-09-02 | Codex | OS右クリックからLibrary Managerを開くFR-22を追加し、Windows実機で検証 |
| 1.4 | 2026-09-02 | Codex | 右クリック表示を`Library Manager`、`Skill Pack: <表示名>`、`Skill: <表示名>`に分類 |
| 1.5 | 2026-09-04 | Codex | 右クリック受付表示、process間排他、異常終了後の再取得をFR-30として追加 |
| 1.6 | 2026-09-05 | Codex | 0.5.9単一direct-root、統合GUI、GitHub-only/no skill install、通常activation時no menu reinstall、明示repairの破壊前rollback検証へ同期 |
