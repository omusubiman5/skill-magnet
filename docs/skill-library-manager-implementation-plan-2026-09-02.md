---
artifact: implementation-plan
version: "1.1"
created: 2026-09-02
source: docs/skill-library-management-requirements.md
status: completed
---

# Skill Library Manager 実装計画

## 追補: pack collectionの一括登録

- 登録元はfolder 1つだけとし、そのfolderを単一skill、1 pack、複数pack collectionの順に自動判定する。
- collection直下のpackと、各pack直下の`SKILL.md`を持つ全folderを候補母集合にする。`INDEX.md`は順序と関係の入力に使うが、INDEXに未記載の実在skillを黙って捨てない。
- pack直下の`SKILL.md`はpack全体を案内するentry skillとして登録し、子skillへの相対linkを公開repository構成に合わせて機械変換する。
- source側の`acceptance.json`は任意とする。不在時はLibrary Managerがrepository契約用の内部互換metadataを生成し、`test-prompts.json`があればそのSHA-256を記録する。
- INDEX参照先欠落、skill／pack ID重複、壊れた関係、必須`SKILL.md`欠落は書込み前に拒否する。copy中の失敗もcatalog、INDEX、追加directoryを一括rollbackし、部分登録を残さない。
- 実データ`C:\Projects\cangjie-skill-clean\books`をsmoke対象とし、3 pack・33 skillの検出数、登録数、catalog数が一致することを合格条件にする。

## 追補: 右クリック製品入口

- FR-22をP4の製品入口へ追加する。
- Windows Explorerのmodern/classic `Skill Magnet`配下へ固定のmanager actionを追加し、選択folderを`library ui --repository`へ一つのargvとして渡す。
- Finderは既存Quick Actionを維持し、共通選択画面内の`Library Manager`ボタンから同じGUIへ遷移する。
- manager actionはpack/skill leaf countへ混入させず、publish/activateの既存明示確認gateを維持する。
- manifest parser、registry、特殊文字path、CLI prefill、Windows/macOS共通callbackを回帰試験する。

## Step 0: Source ledger

- S1: 「skill追加と製品反映をアプリ内の一つのguided flowで完結させる。」（要件定義 / Goals）
- S2: 「固定commit、digest、INDEX関係、pack構成を機械検証し、壊れた更新を有効化しない。」（要件定義 / Goals）
- S3: 「GitHub書込みとローカル設定更新を安全に分離し、途中失敗時も現在の有効版を維持する。」（要件定義 / Goals）
- S4: 「編集作業は利用者の既存checkoutではなく、製品所有のisolated temporary workspaceで行い、未コミット作業を変更してはならない。」（要件定義 / FR-10）
- S5: 「remote検証が完了するまで、現在有効な本体lock/configを変更してはならない。」（要件定義 / FR-13）
- S6: 「retryは同じtransaction IDを使用し、同じcommitを重複作成・重複push・重複登録してはならない。」（要件定義 / FR-21）

## Section 0. Executive summary

- **Situation classification:** Complicated（Cynefin）— Git、GitHub、設定、OSメニューの複数境界を扱うが、期待動作と検証条件はFR-1〜FR-22として明示されている。
- **The binding constraint:** remote公開とローカル有効化を一つの安全な状態機械として扱う実装がないこと（TOC）。
- **The critical next effort (P1):** catalog、検証器、transaction journal、receiptを備えたlibrary domain層を先に実装する。
- **Overall plan confidence:** Medium-High — 既存の固定commit検証とmenu rollbackを再利用できるが、実GitHub権限と実機UXは環境依存である。
- **Time-to-value:** domain層とCLIの自動テスト完了時点で、ローカル／bare remoteを使った公開・有効化flowを再現できる。

## Section 1. Input mirror - what I understand

- **What you gave me:** repository管理、skill作成／import、関係検証、preview、branch／PR公開、remote digest再検証、lock有効化、条件付きmenu更新、復旧、receipt、右クリック入口までを定義した22件の機能要件。
- **What you appear to be trying to accomplish:** 従来の手作業をSkill Magnet自身の製品機能へ移し、安全性を落とさずskill libraryを継続更新できるようにすること。Confidence: High。
- **Adjacent intents I noticed but did not assume:** 既存repositoryのrename、GitHub以外のprovider、LLMによるskill生成は対象外。

## Section 2. Situation classification (Cynefin)

**Domain:** Complicated  
**Source:** S1, S2, S3

必要な因果関係は、検証済みremote commitを作り、その証拠を用いて本体を有効化する順序として把握できる。一方で複数repository、Git状態、OS adapterを跨ぐため単純なCRUDではなく、専門的なtransaction設計とfailure injectionが必要である。したがって分析後に契約へコミットする。

## Section 3. The binding constraint (Theory of Constraints)

- **System and goal:** skill追加から安全な製品有効化までを一つの製品flowで完了する。
- **The constraint:** 公開、remote再検証、有効化、rollbackを統括する永続transaction境界がない。
- **Source:** S2, S3, S5, S6
- **Candidate constraints considered:** UI不足はdomain APIの下流であり、GitHub認証方式はCLI credentialを注入可能にすれば初期実装を阻害しない。
- **Why P1 lifts it:** 状態遷移と証拠schemaを先に固定すれば、CLI/UI、Git、lock、menuを同じfail-closed規則へ接続できる。

## Section 4. Prioritized questions, gaps, and open decisions

| Rank | Question / gap | Why it matters | Decision required? | How to resolve |
|---|---|---|---|---|
| Q1 | catalog filename/schema | repositoryと本体の共通入力になる | No | `skill-magnet.catalog.json` schema v1として実装・testで固定 |
| Q2 | GitHub認証経路 | PR作成可否が環境依存 | No | 既存`git`/`gh` credentialのみ利用し、tokenは受け取らない |
| Q3 | 実機menu更新 | Windows/macOS固有 | No（実装は既存adapter） | menu shape差分時だけ既存transactional installerを呼ぶ |
| Q4 | 実GitHub branch protection | repositoryごとに異なる | No | direct pushを既定禁止、PR URLがない限りpending扱い |

## Section 5. The prioritized action plan

#### P1. Library domainと検証契約

- **Why:** 全操作が依存する安全境界を先に確立する。
- **What:** catalog model、skill/INDEX/acceptance安全検証、relation graph、menu shape、digest manifest。
- **How:** schemaを固定する、path/symlink/secret/frontmatterをfail-closed検証する、dependency cycle・unknown・contrastを検出する、canonical JSONとSHA-256証拠を生成する。
- **Confidence:** Medium-High — 要件と既存検証コードが明確。
- **Source:** S2, S4
- **Expected outcome / success signal:** negative testがすべて拒否され、正常catalogがround-tripする。
- **Estimated effort:** 0.5日。
- **Dependencies:** なし。

#### P2. Isolated publish transaction

- **Why:** binding constraintのremote側を解消する。
- **What:** transaction ID付きclone、overlay、preview、明示確認、commit、non-force push、remote bytes再検証、PR連携、journal。
- **How:** product-owned state配下へcloneする、既存checkoutはread-only入力にする、差分をpreviewへ保存する、確認後だけcommit/pushする、remote commitからmanifestを再計算する。
- **Confidence:** Medium — GitHub policy差はpending状態で吸収する。
- **Source:** S3, S4, S6
- **Expected outcome / success signal:** retryでcommit/pushが重複せず、remote不一致時にactivationへ進まない。
- **Estimated effort:** 0.75日。
- **Dependencies:** P1。

#### P3. Atomic activation、status、receipt

- **Why:** remote成功を安全に製品へ反映し、失敗時も旧版を守る。
- **What:** config backup、atomic replace、menu shape条件分岐、rollback、published-but-inactive表示、sanitized receipt。
- **How:** remote manifest一致をgateにする、config候補をConfigで再読込する、menu失敗時は旧bytesを復元する、状態を5分類する、receiptに秘密を含めない。
- **Confidence:** Medium — 既存menu rollbackを再利用可能。
- **Source:** S3, S5, S6
- **Expected outcome / success signal:** failure injection後も旧configがbyte一致し、statusとreceiptが真の状態を示す。
- **Estimated effort:** 0.5日。
- **Dependencies:** P1、P2。

#### P4. 製品入口と回帰検証

- **Why:** domain機能を利用者がアプリから操作できる必要がある。
- **What:** `skill-magnet library` guided CLI、JSON出力、全FR traceability test、文書更新。
- **How:** init/validate/preview/publish/activate/status/retryをsubcommand化する、確認なし書込みを拒否する、unit/integration testを追加する、全既存testを実行する。
- **Confidence:** Medium-High — 既存CLI形式に合流できる。
- **Source:** S1, S2
- **Expected outcome / success signal:** helpから全flowへ到達でき、既存testを壊さない。
- **Estimated effort:** 0.5日。
- **Dependencies:** P1〜P3。

**Sequencing (Now / Next / Later)**

| Now | Next | Later |
|---|---|---|
| P1 | P2, P3 | P4 |

**What to defer / what NOT to do**

- repositoryを自動renameしない。
- default branch protectionを変更しない。
- API key入力やLLM生成を追加しない。
- GitHub以外のprovider抽象化を先行実装しない。

## Section 6. Risks and pre-mortem

| Risk | Likelihood | Impact | Early signal | Mitigation | Source |
|---|---|---|---|---|---|
| push済みだがconfig更新失敗 | M | H | journalが`verified`で停止 | 旧config維持、`published_but_inactive`表示、同一ID retry | S3, S6 |
| remote bytesとlocal commitが不一致 | L | H | manifest digest差 | remoteから再読込しactivation拒否 | S2, S5 |
| dirty checkoutを誤変更 | L | H | 入力treeのstatus/hash変化 | clone先以外へのwrite禁止test | S4 |
| menuだけ更新失敗 | M | H | installer例外 | configとmenuを直前版へrollback | S3 |
| receiptにcredential混入 | L | H | token形式検出 | command出力を保存せずURLをsanitize | Inferred |

## Section 7. Recommended pm-skill prompts

本計画は実装作業へ直接落とせるため、追加のPM skill実行は不要。P1〜P4を同じ変更セットとして実行する。

## Section 8. Evidence and source map

| Claim / recommendation | Source ID | Exact quote |
|---|---|---|
| guided flowを製品入口にする | S1 | 「skill追加と製品反映をアプリ内の一つのguided flowで完結させる。」 |
| fail-closed検証をP1にする | S2 | 「固定commit、digest、INDEX関係、pack構成を機械検証し、壊れた更新を有効化しない。」 |
| publishとactivationを分離する | S3 | 「GitHub書込みとローカル設定更新を安全に分離し、途中失敗時も現在の有効版を維持する。」 |
| isolated cloneだけを編集する | S4 | 「編集作業は利用者の既存checkoutではなく、製品所有のisolated temporary workspaceで行い、未コミット作業を変更してはならない。」 |
| remote検証をactivation gateにする | S5 | 「remote検証が完了するまで、現在有効な本体lock/configを変更してはならない。」 |
| retryを冪等化する | S6 | 「retryは同じtransaction IDを使用し、同じcommitを重複作成・重複push・重複登録してはならない。」 |

**Inferred (Low confidence) claims:** receiptのcredential検出方式のみ。binding constraintとP1は推論に依存しない。  
**Evidence gaps:** 実GitHub repositoryのbranch protectionと実Mac/Windows UI受入は実行環境固有であり、実装後の外部受入証拠が必要。
