---
artifact: prd
version: "1.3"
created: 2026-09-02
status: implemented
---

# 要件定義: Skill Library Manager

## Overview

### Problem Statement

Skill Magnetは本体repositoryとskill repositoryを分離し、固定commitから安全にskillを読む設計になっている。一方、skill追加時にはrepository編集、INDEX更新、commit、検証、pack設定更新、メニュー再登録を利用者が手作業で行う必要がある。この手順は製品の中核操作なのにアプリ外へ漏れており、digest、固定commit、pack構成の更新漏れを起こしやすい。

また、skill repositoryの名称が特定skillまたは特定packを表すように見えると、一つのrepositoryへ複数領域のskillを継続追加できる「ライブラリ」という役割が伝わらない。repository名は特定skill／packから独立させる必要がある。

### Solution Summary

Skill Magnetへ「Skill Library Manager」を追加する。利用者はアプリ内で汎用skill repositoryを接続または作成し、新規skillの追加、packへの所属、INDEX関係編集、検証、Git commit／push、固定commit確定、本体lock更新、必要時のメニュー再登録までを一つの案内付きtransactionとして完了できる。

Skill Magnetの実行ターゲットはCodex DesktopアプリとClaude Codeデスクトップアプリである。Skill Library Managerが有効化したpackは、この二つのデスクトップアプリへhandoffできる状態にする。

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
| US-2 | skill作者として、SKILL.mdを追加し、trigger／boundary不足を公開前に発見したい | P0 |
| US-3 | pack管理者として、skill所属とdepends-on／composes-with／contrasts-withを画面で編集したい | P0 |
| US-4 | 利用者として、commit、digest計算、本体反映、メニュー更新をアプリへ任せたい | P0 |
| US-5 | repository管理者として、push前に全差分と書込み先を確認し、PR経由で公開したい | P0 |
| US-6 | 利用者として、途中失敗しても現在利用中のpackを壊したくない | P0 |
| US-7 | 保守者として、remote更新とローカル有効版の差分をアプリで確認したい | P1 |

## Scope

### In Scope

- 汎用skill repositoryの作成・接続・状態確認
- 新規skillの作成または既存directoryのimport
- SKILL.md、acceptance.json、INDEX関係、pack catalogの編集と検証
- isolated workspaceでのGit branch、commit、push、PR作成
- full commit SHAと各対象file SHA-256の算出・照合
- 検証済みcommitを参照する本体lock/configの自動更新
- pack/menu変更時だけのWindows Explorer／macOS Finder再登録
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

- FR-5: 利用者は作成済みskill folderだけを指定して登録できなければならない。アプリは`SKILL.md`から内部ID、表示名、目的を取得し、`SKILL.md`または`acceptance.json`がなければ登録前に停止しなければならない。
- FR-6: 公開前検証はskill ID重複、path traversal、symlink、secret候補、必須file、frontmatter、trigger、boundary、acceptance schemaをfail-closedで検査しなければならない。
- FR-7: アプリはskillを既存packへ追加するか、新しいpackを作成する選択を提供しなければならない。
- FR-8: アプリはINDEXの`depends-on`、`composes-with`、`contrasts-with`を編集・検証し、未知skill、dependency cycle、同時選択されるcontrastを拒否しなければならない。

### Publish Transaction

- FR-9: アプリは書込み前にrepository、branch、追加・変更file、pack差分、関係差分、予定される本体更新とメニュー更新を一画面でpreviewし、明示確認を要求しなければならない。
- FR-10: 編集作業は利用者の既存checkoutではなく、製品所有のisolated temporary workspaceで行い、未コミット作業を変更してはならない。
- FR-11: defaultでは専用branchへcommit・pushしてPRを作成し、default branchへの直接pushは明示的に選択され、かつrepository policyが許可する場合だけ実行できなければならない。
- FR-12: remote commit確定後、アプリは40文字commit SHA、INDEX、全対象SKILL.md、acceptance.json、catalogのSHA-256をremote contentから再取得して照合しなければならない。
- FR-13: remote検証が完了するまで、現在有効な本体lock/configを変更してはならない。

### Activation and Menu Update

- FR-14: 検証済みremote commitから、repository URL、固定commit、pack catalog、skill順序、metadata、digestを本体の生成lockへ自動反映しなければならない。
- FR-15: skill本文だけの変更ではメニュー再登録を行わず、pack追加・削除、表示名、skill所属、menu shapeが変わった場合だけ再登録しなければならない。
- FR-16: WindowsとmacOSで同じcommit、pack、skill集合が有効になったことをstatus画面で確認できなければならない。
- FR-17: 更新完了時にrepository、commit、changed files、digest、pack差分、config結果、menu結果、test結果を含むreceiptを表示・保存しなければならない。

### Failure, Recovery, and Status

- FR-18: GitHub書込み成功後に本体更新が失敗した場合、新commitは「published but inactive」と表示し、旧commitを有効なまま維持しなければならない。
- FR-19: 本体lock更新またはメニュー再登録が途中失敗した場合、直前のlockとmenuを復元し、部分成功を完了表示してはならない。
- FR-20: アプリはremote HEAD、検証済みcommit、現在有効なcommit、未公開編集、published-but-inactive更新を区別して表示しなければならない。
- FR-21: retryは同じtransaction IDを使用し、同じcommitを重複作成・重複push・重複登録してはならない。

### User Experience

- FR-22: Skill Library ManagerはOSの右クリック`Skill Magnet`入口から開けなければならない。Windows Explorerでは`Skill Magnet`配下の固定actionとして直接選択でき、macOS Finderでは`Skill Magnet`クイックアクションが開く共通画面内から選択できる。作業用repositoryはアプリ専用state内で自動管理し、利用者へ保存先やrepository名を入力させない。右クリック対象に`SKILL.md`がある場合だけskill import候補へ事前入力し、publishまたはactivateを自動実行してはならない。

基本flowはタブのない1画面とする。作業用repository、catalog、INDEX、validation、preview、activationのためだけの独立画面は設けず、自動処理または同じ画面へ統合する。

1. 右クリック対象が標準構成（同一folderの`SKILL.md`と`acceptance.json`）なら自動importし、登録欄を隠す。作成済みskillを手動で登録する場合だけ同じ画面の上部へfolder指定欄を表示する。画面内でskillを新規作成してはならない。pack情報からcatalogとINDEXを自動生成する。
2. 同じ画面でGitHub URLを入力し、validationと全差分を確認してからPRを明示公開し、merge後のremoteを照合する。OSを自動判定してactivationを明示確認し、active version、menu、testsをreceiptで確認する。

OSは利用者へ選択させず実行環境から自動判定する。URL未入力、標準構成不備、validation失敗、未mergeなどはその操作時のエラーとして表示し、外部書込みまたはactivationを行わない。
手動登録画面で利用者が指定するのは作成済みskill folderだけとする。Skill IDは`SKILL.md`の`name`から自動取得し、`name`がない場合だけfolder名を使う。表示名と目的も`SKILL.md`から取得し、packはアプリ管理の`Custom skills`を使う。`SKILL.md`または`acceptance.json`がなければ登録前にエラー停止する。
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
| GitHub接続が切れた | local temporary編集を安全に保持し、外部成功を主張しない |
| config更新後にmenu登録が失敗した | configとmenuを直前版へrollbackする |
| 既存repository名がdomain固有 | そのまま接続可能。renameを強制・自動実行しない |
| secret候補を含む | commit前に拒否し、該当fileとruleだけを表示する |

## Technical Considerations

### Constraints

- skill contentの唯一の永続的source of truthはユーザー所有GitHub repositoryとする。
- authoring用contentはisolated temporary workspaceに限り、完了・取消・失敗cleanup後に残さない。
- activationは完全なcommit SHAへ固定し、branch名またはHEADを直接参照しない。
- SHA-256はローカル編集物ではなく、push後にremoteから再取得したbytesを基準にする。
- GitHub credentialをproject file、config、receipt、logへ保存しない。
- OpenAI／Anthropic API key、従量課金API、追加支払いを要求しない。
- Windows ExplorerとmacOS Finderの既存安全境界を維持する。

### Integration Points

- GitHub: repository作成・読取、branch push、PR作成、remote commit/content検証
- Git: isolated workspace、diff、commit、non-force push
- Skill Magnet config/lock: 検証済みcommitとpack catalogのactivation
- Explorer/Finder adapter: menu shape変更時のtransactional reinstall
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
| FR-15 | content-only時no reinstall、menu-shape変更時reinstall test | Automated |
| FR-16 | Windows/macOS statusのcommit・skill集合一致CI | Automated |
| FR-17 | receipt schema、secret非混入、全証拠参照test | Automated |
| FR-18 | push後activation失敗のpublished-but-inactive E2E | Automated |
| FR-19 | config/menu各failure pointのrollback E2E | Automated |
| FR-20 | 5状態のstatus表示snapshot test | Automated + QA |
| FR-21 | 同一transaction retryのcommit・push・menu重複ゼロtest | Automated |
| FR-22 | Explorer/Finder右クリック導線、選択folder prefill、no-auto-write test | Automated + Windows QA |

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
| Explorer/Finder transactional reinstall | Engineering | 既存 | menu反映の自動化不可 |
| 実Windows/macOS UX受入 | QA / Product owner | Releaseごと | GA判定不可 |

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| cross-repository更新が途中で分断される | M | H | remote publishとactivationを二段階化し、旧lockを維持 |
| GitHub tokenがlogへ漏れる | L | H | OS credential storage、redaction、secret scan |
| INDEX自動編集が意味を壊す | M | H | relation UI、preview、cycle/contrast validation |
| repository名変更で既存URLが壊れる | M | M | 自動rename禁止、明示migrationのみ |
| menu再登録が不要な変更でも走る | M | M | menu shape diffによる条件実行 |
| temporary workspaceが残る | L | M | product-owned path、journal、startup recovery、residue test |

## Timeline & Milestones

| Milestone | Description | Target Date |
|---|---|---|
| M1: Contract | catalog schema、transaction state、receipt schemaを確定 | 実装開始前 |
| M2: Local Authoring | create/import、validation、pack/INDEX編集、preview | Sprint 1 |
| M3: GitHub Publish | isolated commit、push、PR、remote digest検証 | Sprint 2 |
| M4: Activation | generated lock、menu差分更新、rollback、status | Sprint 3 |
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
| 1.1 | 2026-09-02 | Codex | FR-1〜FR-21の実装・検証完了に伴いstatusをimplementedへ更新 |
| 1.2 | 2026-09-02 | Codex | 実行ターゲットをCodex Desktopアプリ／Claude Codeデスクトップアプリとして明記 |
| 1.3 | 2026-09-02 | Codex | OS右クリックからSkill Library Managerを開くFR-22を追加し、Windows実機で検証 |
