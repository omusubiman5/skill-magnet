# Skill Magnet リリース是正実装計画

> **Superseded:** 0.3.0のGO判定はSmart App Control error 4551の実機反証により撤回しました。現行計画は[`smart-app-control-remediation-plan-2026-08-29.md`](smart-app-control-remediation-plan-2026-08-29.md)です。

## Step 0. Source ledger

- **S1:** 「実装計画書MDを作って、修正して。実装報告書MDを作って。」（ユーザー依頼）
- **S2:** 「テストもすべてやりきる。」（ユーザー依頼）
- **S3:** 「配布wheelが壊れている」（鬼レビューへの注釈）
- **S4:** 「リリース候補commitもgreen CIもない」（鬼レビューへの注釈）
- **S5:** 「製品自身の完成条件が未達」（鬼レビューへの注釈）
- **S6:** 「ありえないだろ」（証明書・絶対path・TOCTOU・残留物・監査資料・版不一致への注釈）
- **S7:** 「やれ、ぶち壊すぞ」（NO-GO是正要求への注釈）

## 0. Executive summary

- **Situation classification:** Complicated（Cynefin）。故障点は配布、CI、証明書、Desktop/Finder受入、監査資料に分解でき、各原因と修正方法を技術的に検証できる。
- **The binding constraint:** 空環境で再現できる単一のリリースartifactが存在しないこと（Theory of Constraints）。
- **The critical next effort (P1):** wheel/source releaseからnative資材と固定packを解決できるようにし、隔離環境のartifact-first smokeを必須化する。
- **Overall plan confidence:** High。Windows/macOS CI、Windowsの実MSIX lifecycle、macOSの実Automator lifecycle、Windows ExplorerからDesktop handoffまでgreen。旧自己署名証明書のupgrade cleanupも実機とCI fixtureの双方で確認した。残る製品外ゲートはCodex Desktop新規taskの自然文回答確認である。
- **Time-to-value:** P1の最初の信号は、隔離wheelでnative path・status・manifest生成が成功した時点で得る。

## 1. Input mirror

- **What you gave me:** NO-GO項目を報告で終わらせず、計画書、実装修正、全試験、報告書まで完遂する要求。
- **What you appear to be trying to accomplish:** Skill Magnetを開発PC限定の動作品ではなく、再現可能で監査可能なリリース候補へすること。Confidence: High。
- **Adjacent intents not assumed:** 公開PyPI、Microsoft Store、正式コード署名証明書の購入・公開配布は、外部アカウントと配布権限が必要なため自動では実行しない。

## 2. Situation classification (Cynefin)

**Domain:** Complicated  
**Source:** S1-S7

原因は既知で、artifact内容、path解決、CI checkout、証明書ownership、handoff証拠、cleanup、文書ledgerを個別に試験できる。未知のユーザー行動ではなく、専門的解析で正誤を決められるためComplexではない。実装後は段階的commit候補ではなく、単一candidate全体でrelease gateを通す。

## 3. Binding constraint (Theory of Constraints)

- **System and goal:** Skill Magnetを第三者が空環境へ導入し、同一packを安全に実行・rollbackできる状態にする。
- **Constraint:** 配布artifactがnative資材とportableなpack解決を所有していない。
- **Source:** S3, S4, S5
- **Candidate constraints considered:** 赤いCIはartifact欠陥を検出する下流ゲート。Desktop/Finder受入は導入可能なartifactができた後の下流検証。
- **Why P1 lifts it:** wheel単独動作とfresh-install smokeが成立すれば、CI・実機受入・rollbackを同じ候補物で評価できる。

## 4. Prioritized questions, gaps, and open decisions

| Rank | Question / gap | Why it matters | Decision required? | How to resolve |
|---|---|---|---|---|
| Q1 | 配布単位をwheel＋native source bundleにできるか | installerがrepository rootへ依存している | No | wheel内容と隔離install smokeで検証 |
| Q2 | 固定packを別PCでどう取得するか | `C:\Projects`固定を排除する必要がある | No | config-relative sourceとsubmodule/bootstrapを実装 |
| Q3 | 自己署名証明書ownershipを更新間で保持できるか | uninstall後のmachine trust残留を防ぐ | No | ownership stateをmergeしstore全件をnegative test |
| Q4 | Desktopの読込bytesを固定できるか | path参照TOCTOUを除去する | No | contract専用content-addressed materialization |
| Q5 | macOS Finder workflowを実macOS hostで実行できるか | Windows fixtureだけではQuick Actionの実行を証明できない | Resolved | macOS runnerでinstall、`/usr/bin/automator`実行、selected path probe、uninstall、残留ゼロを必須化 |

## 5. Prioritized action plan

### P1. 再現可能な配布artifact

- **Why:** binding constraintを直接解消する。
- **What:** native資材を含み、開発checkout外でもpathを解決できるwheelとsource導入経路。
- **How:** build hookでnative資材をwheelへ収録する。runtimeはpackage内資材を優先する。config sourceをconfig-relativeにする。submodule checkoutをCIへ追加する。隔離wheel smokeを追加する。
- **Confidence:** Medium-High。wheel欠落は再現済みで修正結果を機械検査できる。
- **Source:** S3, S4
- **Expected outcome / success signal:** 空directory/venvからnative scripts、manifest、menu生成、CLI statusが利用できる。
- **Estimated effort:** 1実装サイクル。
- **Dependencies:** none。

### P2. 証明書・install・rollbackの所有境界

- **Why:** 配布可能でもuninstallがtrustを残すなら安全なreleaseではない。
- **What:** updateをまたぐcertificate ownership、正確なcleanup、残留回収。
- **How:** state mergeを実装し、既存ownershipをfalseで上書きしない。cleanup対象thumbprintを固定する。interrupted backup recoveryを追加する。install→update→rollback→uninstall testを追加する。
- **Confidence:** Medium-High。
- **Source:** S6
- **Expected outcome / success signal:** owned certificateとinterrupted backupが最終状態でゼロ。
- **Estimated effort:** 1実装サイクル。
- **Dependencies:** P1 artifact layout。

### P3. Desktop/Finderの固定入力と完了証拠

- **Why:** CLI成功を製品Desktop成功へ誤転用しないため。
- **What:** content-addressed read-only handoff、Desktop実機受入記録、Finder実workflow lifecycle。
- **How:** contract専用immutable materializationを作る。promptをそのpath/digestへ束縛する。handoffは未完了のまま保持する。macOSでは実Quick ActionをinstallしてAutomatorから起動し、WindowsではExplorerの実メニューからDesktop handoffまで操作する。
- **Confidence:** Medium。Desktop結果の機械取得不可部分は人手ゲートが残る。
- **Source:** S5, S6
- **Expected outcome / success signal:** prompt後のsource変更でhandoff bytesが変わらず、Desktop/Finder証拠がcandidate SHAへ結び付く。
- **Estimated effort:** 1-2実装サイクル。
- **Dependencies:** P1。

### P4. release gate・監査資料・版管理

- **Why:**正しい実装を誤った古いledgerで判定しないため。
- **What:** package仕様対応ledger、復元したgate、artifact-first CI、統一version、clean tree規則。
- **How:**旧18-leaf証拠を置換する。gate/testsを新仕様で復元する。Windows/macOS matrixへsubmodule・wheel smoke・native gateを追加する。生成物ignoreとversion同期を実装する。
- **Confidence:** High。
- **Source:** S2, S4, S6
- **Expected outcome / success signal:** ledger、test count、package leaf、candidate SHA、artifact hashが一致する。
- **Estimated effort:** 1実装サイクル。
- **Dependencies:** P1-P3。

**Sequencing**

| Now | Next | Later |
|---|---|---|
| P1 | P2, P3 | P4と外部CI/実機gate |

**What not to do**

- ローカル108 testsだけでrelease可能と宣言しない。
- CLI `verified_completed`をDesktop完成証拠へ転用しない。
- 赤いCI、skip、未追跡生成物を残したままtagを作らない。

## 6. Risks and pre-mortem

| Risk | Likelihood | Impact | Early signal | Mitigation | Source |
|---|---|---|---|---|---|
| wheel内native pathがeditable installと異なる | M | H | isolated smokeだけ失敗 | repo/wheel両layoutを同一testで検証 | S3 |
| certificate ownershipが再更新で消える | M | H | update後stateがfalseへ戻る | merge semanticsと2回更新test | S6 |
| immutable handoffがURL上限を再超過 | M | M | deep-link長test失敗 | bytesはfile化しpromptはdigest/pathのみ | S5 |
| macOSだけpath/permissionで失敗 | M | H | macOS matrix赤 | recursive submodule＋Finder fixture＋実機gate | S4, S5 |
|古い監査資料が再び正本化される | M | M | ledger count/leaf不一致 | gateをCI必須化 | S6 |

## 7. Recommended execution prompts

本計画はコード修正を直接実行するため、追加PM skillへのhandoffは行わない。各P項目をrepository実装・試験としてこのタスク内で処理する。

## 8. Evidence and source map

| Claim / recommendation | Source ID | Exact quote |
|---|---|---|
| 計画・修正・報告を一体で完遂する | S1 | 「実装計画書MDを作って、修正して。実装報告書MDを作って。」 |
| 全試験をrelease gateまで実行する | S2 | 「テストもすべてやりきる。」 |
| P1はartifact単独動作 | S3 | 「配布wheelが壊れている」 |
| candidate/CIを必須化 | S4 | 「リリース候補commitもgreen CIもない」 |
| Desktop/Finder完成条件を満たす | S5 | 「製品自身の完成条件が未達」 |
| P1-P3残存を全件修正 | S6 | 「ありえないだろ」 |

**Inferred claims:** 公開Store/PyPIへの実配布は今回の権限範囲外。binding constraintとP1は推論ではなくS3-S5に基づく。  

## 9. 実行結果

- P1-P4の実装項目は完了した。
- 証明書cleanup実装基準commitは`668d45b2460955b39d7af97df2aa7ea7d379f6e5`、cleanup証拠をCLI結果へ保持する実装は`76aad58`。
- release code SHA `b4f68209a2c898879c3f279ce7080ca7301a186b`のGitHub Actions run [33248318073](https://github.com/omusubiman5/skill-magnet/actions/runs/33248318073)でWindows/macOSの120 testsとcanonical wheel gateがgreen。CI actionもNode 24対応版へ更新し、廃止runtime警告を除去した。
- Windows jobでは追加でcertificate ownership、wheel-installed native build、実MSIX初回install→update→直前版rollback→uninstall、製品所有証明書・Appx・registry・external root・rollback pointの残留ゼロがgreen。
- macOS jobでは実Quick Actionのinstall、`/usr/bin/automator`実行、通常の製品adapter到達、uninstall、transaction residueゼロがgreen。
- Windows 0.3.0実機で、BEADS folderの右クリックから`Skill Magnet`→単一`PMO`→実行確認→Codex→最終確認を操作し、pack 9件を束縛したcontract `d372a02620e84f01a9a6e326d1826ba7`の`desktop_handoff_ready`を確認した。
- Windows 0.3.0実機upgradeで旧`CN=Skill Magnet Local` thumbprint 7件を削除した。現行実装は属性一致から所有権を推定せず、stateの`owned_certificate_thumbprints`だけをcleanup対象にする。現行thumbprint `022B95CF60214A2F7A36BE33E1112B5A62831561`だけが3 storeに残り、modern menuは`usable_installed_state: true`である。
- Codex Desktop新規taskの自然文回答をユーザーが確認した。回答はINDEXルーティングに従うPMOパックの組合せ適用、具体的なBEADS文書レビュー、対象外能力の不適用、ファイル変更なしを示した。handoff証拠自体は回答完了へ昇格させず、ユーザー実機受入として別記録した。

**Release boundary:** このrepositoryが定義するローカル自己署名版0.3.0は全実装ゲートと実機受入を完了し`GO`。Microsoft Store等の公開配布は製品仕様外で、正式publisher identity・配布アカウント・秘密鍵が別途必要であり、このローカル自己署名候補を公開Store版GOとは扱わない。
