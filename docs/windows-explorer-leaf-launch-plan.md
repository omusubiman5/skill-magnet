# Windows Explorer leaf即時起動 UX 改訂計画

## 文書の位置づけと追跡

この文書は、Windows Explorerの軽量UX改訂を実装する前の凍結計画です。実装、レジストリ変更、実ユーザー起動、監査依頼はこの文書の作成範囲に含めません。

- 親ゴールMD: [`mvp-redesign.md`](mvp-redesign.md#目的成果物完了条件)
- 規範ポリシー: [`../policy/product-policy.json`](../policy/product-policy.json)
- 親Issue: `PM-011`（目的・成果物・手動起動条件・完了条件）
- 直前の実装・監査Issue: `PM-020`（固定版skillのCodex読込・適用証拠）
- 改訂要求のoriginating task: `01a027ac-dd95-7bc1-bad1-c3d488eb7325`
- 改訂Beads epic: `sm-62a`（Windows Explorer leaf即時起動 UX 改訂）
- 実行結果MD: [`windows-explorer-leaf-launch-results.md`](windows-explorer-leaf-launch-results.md)
- 9個別skillテスト計画MD: [`individual-skill-test-plan.md`](individual-skill-test-plan.md)

### Beads子Issueと依存順

全Issueはこの計画書を `spec-id` として参照します。`sm-62a.3`〜`.6` は作業を保持しない非実行umbrellaです。実作業Issueはすべて見積り10分で、下表の一項目だけを成果とします。閉鎖済みの `PM-020` は新実装Issueとして再利用しません。

| Beads ID | 作業またはumbrella | 直接のblocking dependency |
| --- | --- | --- |
| `sm-62a.1` | 改訂計画MDの独立監査（10分） | なし |
| `sm-62a.2` | fixed commitの再レビュー・承認記録（10分） | `.1` |
| `sm-62a.3` | registry argv引用契約umbrella（非実行） | `.1` |
| `sm-62a.3.1` | argv command builder（10分） | `.1` |
| `sm-62a.3.2` / `.3.3` | `Directory %1` / `Background %V` 登録（各10分） | `.3.1` |
| `sm-62a.3.4` | 両root特殊path argv自動テスト（10分） | `.3.2`、`.3.3` |
| `sm-62a.3.5` | reinstall・限定uninstall検証（10分、旧pack-only基盤） | `.3.4` |
| `sm-62a.3.6` | 個別skill階層menu contract（10分） | `.8`、`.3.5` |
| `sm-62a.3.7` | 個別skill両root引用・stale menu検証（10分） | `.3.6` |
| `sm-62a.4` | artifact policy umbrella（非実行） | `.1` |
| `sm-62a.4.1` | cleanup先行terminal確定順（10分） | `.1` |
| `sm-62a.4.2` | 事前拒否・起動失敗artifact（10分） | `.4.1` |
| `sm-62a.4.3` | output・acceptance・cleanup失敗artifact（10分） | `.4.2` |
| `sm-62a.4.4` | 強制中断public recovery（10分） | `.4.3` |
| `sm-62a.4.5` | Claude拒否と保持表テスト（10分） | `.4.4` |
| `sm-62a.5` | 注入E2E umbrella（非実行） | `.1` |
| `sm-62a.5.1` | 両rootの個別skill menu-contract伝播E2E（10分） | `.2`、`.3.7`、`.4.5`、`.8` |
| `sm-62a.5.2` / `.5.3` | Codex silent success / 事前拒否注入（各10分） | `.5.1` |
| `sm-62a.5.4` | 起動・出力・cleanup失敗注入（10分） | `.5.2`、`.5.3` |
| `sm-62a.5.5` | 強制中断recovery E2E（10分） | `.5.4` |
| `sm-62a.5.6` | Claude副作用ゼロ・project不変E2E（10分） | `.5.5` |
| `sm-62a.5.7` | 9 skills個別Codex E2E証拠行列umbrella（非実行） | `.8` |
| `sm-62a.5.7.1`〜`.5.7.9` | 9 skillsを一件ずつ実行・証拠化（各10分） | `.5.6` |
| `sm-62a.5.8` | 9 skills個別E2E行列の集約検証（10分） | `.5.7.1`〜`.5.7.9` |
| `sm-62a.5.9` | Explorer menu cancel無副作用E2E（10分） | `.5.1` |
| `sm-62a.6` | Explorer実機証拠umbrella（非実行） | `.1` |
| `sm-62a.6.1` | registry snapshot・復元準備（10分） | `.5.6`、`.5.8`、`.5.9` |
| `sm-62a.6.2` | Directory特殊path Codex成功（10分） | `.6.1` |
| `sm-62a.6.3` | Background特殊path Codex成功（10分） | `.6.2` |
| `sm-62a.6.4` | fixed版不一致error UI（10分） | `.6.3` |
| `sm-62a.6.5` | Codex強制中断復旧（10分） | `.6.4` |
| `sm-62a.6.6` | Claude一回error・最終復元（10分） | `.6.5` |
| `sm-62a.7` | 独立実装監査（10分） | `.6.6` |
| `sm-62a.8` | 個別skill選択改訂の独立計画監査（10分） | なし。PASS前は個別skill実装禁止 |

依存関係の機械可読な正本は `C:\Projects\skill-magnet\.beads` のBeads databaseです。この表との不一致があれば実装を開始せず、Beadsと計画MDを再照合します。

### 実行結果MD closeゲート

計画と実行結果を同じ文書で上書きしません。要件・受入条件はこの計画MD、実行した変更、テスト、証拠、cleanup、停止理由、ready/blocked状態は [`windows-explorer-leaf-launch-results.md`](windows-explorer-leaf-launch-results.md) を正本とします。

各実行Issueは、結果MDへIssue ID、結果、テストcommand/result、artifactまたは実機証拠、cleanup状態、次のready/blockedを追記し、`git diff --check` を通し、Beads commentと結果MDを相互参照した後にだけcloseできます。結果MD未追記のIssueをcloseしてはなりません。親epicとumbrellaは、全実行子Issue、結果MD、Explorer実機証拠、最終独立実装監査が完了するまでcloseしません。

## 目的と要件

ユーザーが右クリックメニューでpack、個別skill、対象AIを明示した時だけ、GitHubのユーザー所有保管庫にある承認済み固定版を検証し、その対象AIを即時起動します。ローカル配置だけを成功とせず、選択した一件のskill固有acceptanceを満たした `verified_applied` 証拠まで取得します。

必須要件は次のとおりです。

- MVPの選択粒度は個別skillとする。メニューは `Pack: <id> → Skill: <skill-id> → Codex / Claude` と表示する。
- 通常のExplorer経路は `右クリック → Skill Magnet → Pack: <id> → Skill: <skill-id> → Codex / Claude` とする。
- `Codex` または `Claude` leafの選択を、そのpack、個別skill、対象project、対象AIに対する明示的な起動同意とする。対象skillまたは対象AIが曖昧な経路は作らない。
- pack全体選択が将来必要な場合は、`All skills (<N>)` 等の明示的な別leafと `selection_kind=pack` の別contractとして再計画する。個別skill leafから暗黙にpack全体へ拡張しない。
- 通常確認UIは出さない。purposeとverificationは承認済みpack定義の既定値を使い、leafで選んだruntimeは以後変更できない。
- 固定版、owner、origin、approval、acceptance、project pathをバックグラウンドで検証し、どれか一つでも保証できなければfail-closedにする。
- 成功時は確認ダイアログやtoastを表示せず、選択runtimeの検証済みsessionを利用できる対話terminalとして開く。失敗時だけ、秘密情報や内部prompt、traceを含まない人間向けエラーを一回表示する。
- 全件同期、常設skill配置、自動提案、自動配布、自動有効化を行わない。一時取得物は検証・実行後に片付ける。

## 選択フローと対象AI

1. ユーザーがExplorerでprojectフォルダー本体、または開いているprojectフォルダーの背景を右クリックする。
2. `Skill Magnet` の下から `Pack: <id>` を選ぶ。
3. pack配下から `Skill: <skill-id>` を一件選ぶ。
4. skill配下の `Codex` または `Claude` leafを選ぶ。この操作でproject、pack、selected skill、runtimeが固定され、通常確認UIを介さず共通コアへ渡る。
5. 共通コアがproject、GitHub owner/origin、承認済み40桁commit、選択skillのinstruction digest、acceptance digest、既定purpose/verificationを検証する。
6. launch contractへ `selection_kind=skill`、pack ID、selected skill ID、instruction digest、acceptance digest、commit SHA、runtime、projectを固定する。
7. 検証成功時だけ対象AIを起動する。Codexは選択した一件のskillについてoutput schema、events、skill固有acceptanceを検証し、すべて成功した場合だけ `verified_applied` とする。
8. 確認ダイアログや成功通知は表示しない。検証成功後は同じsessionを選択したCodex/Claudeの実対話アプリへ引き継ぎ、利用可能なterminalを表示する。拒否、起動失敗、出力不正、検証不合格、中断復旧は失敗UIを一回だけ表示する。

Claudeのleafも対象AIを明示固定する。ただし、Claudeの正式なverified activation経路が未実装または保証不能な間は起動せず、fail-closedの一回エラーと監査eventだけを残す。Codexへ代替起動してはならない。

## バックグラウンド検証

leaf選択後、UIを表示する前提だった情報を省略するのではなく、次を非対話で検証します。

- 選択したmenu leafのpack ID、`selection_kind=skill`、selected skill IDが現在の設定と固定commitに完全一致する。
- runtimeは選択したleaf値と一致し、CLI引数、contract、events、evidenceの全段で不変である。
- projectは存在するディレクトリであり、Explorerから渡された一つの正規化済みパスと一致する。
- repository URLのownerが許可リストにあり、originがpack定義と一致する。
- source checkoutがcleanで、`HEAD` が承認済み `expected_commit` と一致する。
- approval情報、選択skillの `acceptance.json`、instruction digest、acceptance digest、pack既定purpose/verificationが固定版から再計算した値と一致する。
- symlink/junction、secret混入、期限切れ・再利用・改変済みcontractなど既存の安全拒否条件を維持する。

検証の通過はskill使用成功ではありません。Codexが選択した一件のskill ID/digestを読込識別し、そのskill固有acceptance結果まで一致した場合だけ、そのskillについて `verified_applied` とします。別skillやpack全体の成功へ拡張しません。

## Explorer登録とargv引用契約

登録対象は現在ユーザーのHKCU配下にあるSkill Magnet固有subtreeだけです。

- フォルダー本体: `Directory` root。Explorer placeholderは `%1`。
- フォルダー背景: `Directory\\Background` root。Explorer placeholderは `%V`。
- installは両rootのSkill Magnet固有subtreeを再生成し、旧packのstale leafを除去する。
- uninstallはSkill Magnet自身の両subtreeだけを削除し、他製品や親rootを変更しない。
- pack設定を変更した後はmenu reinstallを必須とし、設定とleafの不一致を起動時にも拒否する。

引用契約は次のとおりです。

- `cmd.exe /c`、文字列連結したshell command、未引用placeholderを使わない。
- Explorerによる置換後の `%1` または `%V` は、project pathを表す単一argv要素としてrunnerへ渡す。末尾backslash、埋込み引用符、環境変数展開でargv境界が変化しない登録形式を使う。
- Python executable、runner/module、config path、pack ID、selected skill ID、runtimeもそれぞれ独立したargv要素として安全に引用する。
- project pathやconfig pathをshellで再解釈、展開、評価しない。runtimeとpack IDは許可値との完全一致を要求する。
- 空白、日本語、およびWindowsで有効な特殊文字 `& ( ) ' ! ^ # %` を含むproject/config pathを対象にする。Windowsでファイル名として無効な文字をテスト契約へ混ぜない。

両rootの自動テストに加え、実Explorerのフォルダー本体と背景から特殊pathを一回ずつ渡し、contractに記録されたprojectが元の絶対pathと完全一致することを証拠化します。

## artifactの保持とcleanup

旧設計の「すべて残留ゼロ」を無条件には引き継ぎません。監査に必要な不変記録と、再生成可能または秘密を含み得る一時物を分離します。

成功候補は、output schemaと選択skillのacceptanceを通過しても直ちに `verified_applied` にしません。まず非terminalな検証済み候補として扱い、clone、stage、temp prompt、raw output、process markerのcleanupを完了します。cleanup成功後の一つの原子的な更新でだけterminal `verified_applied` eventとevidenceを確定します。cleanupが失敗した場合は成功候補を破棄し、terminal `cleanup_failed` とnegative evidenceを確定します。したがって、一つのattemptに `verified_applied` と `cleanup_failed` が共存することはありません。

| 結果 | contract | output schema | raw output | events | evidence | clone/stage/temp prompt・出力/process marker |
| --- | --- | --- | --- | --- | --- | --- |
| Codex/Claude成功 | 保持 | digestとversionを保持 | 検証後削除。必要最小のsanitized digestのみ保持 | terminal `verified_applied` まで保持 | `verified_applied`、acceptance結果、再開session ID、実runtime PID/argvを保持 | 検証用一時物は原子的に全削除。対話runtimeは利用者が終了するまで保持し、試験時は所有PIDだけをcleanup |
| 事前拒否 | 作成しない | 作成しない | 作成しない | sanitized `rejected` eventとattempt IDを保持 | 成功evidenceは作成しない | 作成済み一時物を全削除 |
| Codex起動失敗 | 保持 | digestとversionを保持 | 存在すれば検証後削除 | terminal `launch_failed` を保持 | negative evidenceを保持し、`verified_applied` は禁止 | 全削除 |
| Codex出力・schema・acceptance失敗 | 保持 | digestとversionを保持 | 検証後削除。秘密を除いた失敗分類/digestのみ保持 | terminal `output_failed` または `acceptance_failed` を保持 | negative evidenceを保持し、`verified_applied` は禁止 | 全削除 |
| cleanup失敗 | 保持 | digestとversionを保持 | 削除を再試行し、削除不能物の内容ではなくsanitized path/digestだけを残留manifestへ保持 | terminal `cleanup_failed` を保持 | negative evidenceを保持し、`verified_applied` は禁止 | best effortで回収。未回収物はattempt ID、期限、再試行状態をmanifestへ記録し、次回public recoveryで再試行 |
| 強制中断 | 保持。次回復旧でterminal化 | digestとversionを保持 | 次回復旧で削除 | 次回public entryで `interrupted` を追記して保持 | negative/interrupted evidenceを保持し、`verified_applied` は禁止 | 次回復旧で全削除しprocess markerも除去 |
| Claude成功 | verified activation実装までは該当なし | 該当なし | 作成しない | 該当なし | 該当なし | 作成しない |
| runtime拒否・失敗 | 事前拒否なら作成しない。起動後失敗は保持 | 実装済みの場合のみdigest/versionを保持 | 存在すれば検証後削除 | sanitized `rejected` またはterminal failureを保持 | 成功evidenceは禁止。negative evidenceのみ | 全削除 |

contract、events、evidenceには秘密、完全prompt、認証情報、不要なモデル出力を保存しません。事前拒否は `events/<attempt-id>-rejected.json`、contract作成後のsanitized lifecycleは `events/<contract-id>-lifecycle.jsonl` に保持し、最終行を一意のterminal eventとします。contract、lifecycle、evidenceはattempt ID、contract ID、terminal event IDで相互参照し、原子的に書き込みます。raw Codex events `evidence/<contract-id>-events.jsonl` は一時物としてcleanupします。cleanup失敗は成功として隠さずterminal failureにし、未回収物の存在と回復予定を利用者へ通知します。保持期限や明示削除機能は別Issueとし、本改訂で監査artifactを自動削除しません。

## 固定版不一致と更新案内

現在、pack source `C:\Projects\codex-pmo-skills-public` の `HEAD` は `225acbe63d0eecdc3617f443dcb495168a482ef4`、設定の承認済み `expected_commit` は `c7747bba0bc391316aa558b3b4e8dd412045d2dc` で一致していません。この状態ではleafを実行せずfail-closedにします。

失敗UIには秘密を出さず、pack ID、承認済みSHA、現在のsource HEAD、不一致理由、次の安全な選択肢を表示します。

1. ローカル変更を失わない方法でsourceを承認済みSHAへ戻す。
2. 新しいHEADを利用したい場合は、内容と全acceptanceを再レビューする。
3. ユーザー承認を新たに記録し、configの `expected_commit`、approval、skill/instruction/acceptance digestを同じ承認単位で更新する。
4. context menuをreinstallし、表示leafのpack ID、9個別skill IDs、各instruction/acceptance digestが更新後configと一致することを確認する。
5. sourceがclean HEADで、origin/owner/固定SHA/digest/approval/acceptanceがすべて一致することを再確認する。
6. Explorer leafからCodexを実行し、`verified_applied` を取得する。

このゲートを途中まで満たした状態、未承認HEAD、dirty source、古いmenu leafでは起動しません。自動checkout、自動commit採用、自動approval、自動menu更新は行いません。

## 自動テスト

次を実装前の受入テストとして固定します。

- `Directory/%1` と `Directory\\Background/%V` の両rootが、全packの全個別skillと `Codex` / `Claude` leafを同じ意味で生成する。
- pack ID、`selection_kind=skill`、selected skill ID、instruction digest、acceptance digest、runtime、project、config pathがmenuから共通コア、contract、events、evidenceへ欠落・置換なく伝播する。
- 空白、日本語、`& ( ) ' ! ^ # %` を含むproject pathとconfig pathを両rootでparameterized testし、各pathが単一argvで完全一致する。
- 個別skill一覧が固定commitの承認済み9 skill IDsと一致し、旧pack-only実行leaf、stale leaf、曖昧skill/runtime経路がない。
- [`individual-skill-test-plan.md`](individual-skill-test-plan.md) の `SM-SK-001`〜`009` を一件ずつ実行し、各contractがselected skill一件だけを持ち、そのskillのdigest/acceptance/evidenceだけで `verified_applied` になる。別skill混入はfail-closedにする。
- Codex/Claude成功では確認ダイアログやtoastを生成せず、`verified_applied` を確定した同じsessionの対話terminalを開く。実runtime PID/argvとwindow到達をevidenceへ残す。
- 事前拒否、固定SHA不一致、owner/origin不一致、approval不足、acceptance不足、secret/symlink/junction、起動失敗、schema不一致、出力失敗、acceptance失敗、cleanup失敗を注入し、一回エラー、正しいterminal event、`verified_applied` 不在を確認する。
- 強制中断を注入し、新しいEngineのpublic entryによる復旧で `interrupted` terminal event、negative evidence、一時物/process marker削除を確認する。
- Claude leafはCodexへフォールバックせず、実Claude adapterでschema/acceptanceを検証した同じsessionをClaude対話画面へ引き継ぐ。Claude executable、session、またはwindow handoffが成立しない場合はfail-closedとする。
- success、failure、interruptionごとに上表どおりcontract/schema/output/events/evidence/tempが保持または削除される。
- reinstallが両rootのSkill Magnet固有stale subtreeだけを置換し、uninstallが自分のsubtree以外を変更しない。
- `.agents`、`.claude`、旧sync state、対象project内容が前後のhashとgit statusで不変である。
- source/config固定版不一致の失敗UIに、再レビュー承認からmenu reinstall、clean HEAD、Explorer再検証までの案内が含まれる。

## Explorer実機証拠行列

実機試験は隔離state、内容を変更しないsafe target、試験用登録を使い、開始前に両registry subtreeをsnapshotし、終了後に完全復元します。実ユーザーのskill常設配置や全件syncは行いません。

| Explorer入口 | path | leaf | 注入条件 | 期待証拠・UI | cleanup/不変条件 |
| --- | --- | --- | --- | --- | --- |
| フォルダー本体 | 空白・日本語・特殊文字を含むpath | 個別skill → Codex | なし | project/pack/selected skill ID/digest/acceptance/runtime/fixed SHA/default purposeが一致し、そのskillだけ `verified_applied`。同じsessionの実Codex PID/argvと対話terminal到達 | 検証一時物なし。project、`.agents`、`.claude`、旧state不変 |
| フォルダー背景 | 同等の別path | 個別skill → Codex | なし | `%V` とselected skillが同一値として伝播し、そのskillだけ `verified_applied`。同じsessionの実Codex PID/argvと対話terminal到達 | 同上 |
| 本体・背景 | safe path | Codex | 固定SHA、owner/origin、approval、acceptance、起動、schema、output、acceptance、cleanupの各失敗を一件ずつ注入 | 各ケースで一回の適切な失敗UI、terminal failure/rejected、`verified_applied` なし | artifact表どおり保持し、一時物を回収 |
| 本体または背景 | safe path | Codex | 実processを強制中断 | 次回起動時に `interrupted` eventとnegative evidence、成功表示なし | process markerと全一時物を回収 |
| 本体または背景 | 特殊path | Claude | 実Claude adapter | 選択skillだけ `verified_applied`、Codexへのfallback 0、同じsessionの実Claude PID/argvと対話terminal到達 | 検証一時物なし、project不変。試験終了時は所有PIDのみcleanup |

実機証拠には、registry export/diff、選択したroot/leaf、argvを秘密除去して比較した結果、attempt ID、terminal event/evidence、UI表示回数、cleanup後のfilesystem/registry/project diffを含めます。スクリーンショットだけを起動・適用成功の証拠にはしません。

## 失敗時UI

失敗UIは一回だけ表示し、短い要約、失敗した安全ゲート、pack ID、selected skill ID、対象AI、利用者が行える次の操作、監査用attempt IDを示します。source HEAD不一致では前述の更新ゲートを案内します。認証情報、完全prompt、内部trace、raw model outputは表示しません。

- 事前拒否のerror UI表示自体が失敗した場合は、sanitized `rejected` eventへ `ui_delivery_failed` を記録し、AIを起動しません。
- AI起動後のlaunch/output/acceptance/cleanup/interruption failureでerror UI表示自体が失敗した場合は、AIがすでに起動済みである事実を保持します。元のterminal failureとnegative evidenceへ `ui_delivery_failed` を追記し、通常のcleanupまたはpublic recoveryを続けます。「AI未起動」とは記録しません。

## 非目標

- Windows 11 modern context menuへの直接統合は本改訂の対象外です。MVPは `Show more options` 側のHKCU静的cascadeを使用します。
- Finder側のUX変更、pack自動提案、設定変更の自動menu更新は行いません。
- 個別skill leafからの暗黙pack全体選択は行いません。pack全体leafは別計画・別contractとして承認されるまで追加しません。
- Codex/Claudeへの全件・常設install、暗黙activation、対象project内容の変更は行いません。
- Claudeの保証済みactivation経路が確認・実装されていない状態を製品成功とは扱いません。

## 実装開始ゲート

実装は、次の条件をすべて満たした後に別作業として開始します。

1. 個別skill改訂計画と [`individual-skill-test-plan.md`](individual-skill-test-plan.md) が `sm-62a.8` の独立計画監査でPASSする。
2. 改訂Beads epic `sm-62a`、子Issue `sm-62a.1`〜`.8`、registry/artifact/E2E/Explorerの全孫Issue、9-skill行列 `sm-62a.5.7.1`〜`.5.7.9` が計画MDを参照し、上表どおりの依存関係を維持する。
3. source HEAD不一致について、承認済みSHAへ戻すか新HEADをレビュー・承認するかをユーザーが選ぶ。
4. frozen scopeを10分以下のIssueへ分割し、計画外機能を追加しない。
5. 各実行Issueのclose前に実行結果MDを更新し、Beads commentとの相互参照とdiff checkを完了する。
