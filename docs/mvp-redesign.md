# GitHub中心MVP再設計

## 規範

製品ポリシーの唯一の規範的な定義は [`../policy/product-policy.json`](../policy/product-policy.json) です。

<!-- product-policy:begin -->
- GitHubのユーザー所有保管庫を唯一の正本とする。
- Skill Magnetはスキルの目的に沿って、必要なパックだけを明示選択して呼び出す。
- Codex/Claudeへの全件・常設・暗黙同期を既定にしない。
- 一時ローカル展開が技術的に必要な場合も、対象・理由・期限・cleanupを明示し、検証後に片付ける。
- 保管庫の版・来歴・承認を保持する。
- ローカル配置の成功を、スキルの読み込み成功または使用成功とみなさない。
- 選択したpackとversionをタスクへ明示し、読み込み証拠とスキル固有の適用証拠を検証する。
- 公式に確認できる経路または必要な証拠がない場合はfail-closedで停止し、保証外であることを明示する。
- 起動はユーザーの右クリックメニューからの明示選択を条件とし、自動提案・自動配布・自動有効化をしない。
- Windows ExplorerとmacOS Finderで同じ選択・確認・起動の意味と安全ポリシーを提供する。
<!-- product-policy:end -->

## 目的・成果物・完了条件

目的は、ユーザー所有GitHub保管庫の固定commitから目的に合うskill packを一つ選び、INDEXで関係づけられた全skillの指示とactual requestをCodex Desktop appの新規taskへ一体でhandoffし、全skillを読んだうえでtrigger/boundaryに合う必要最小集合だけを組み合わせることです。

成果物は二つです。

1. Skill Magnet本体。共通コア、CLI、Windows/macOS UIアダプター、検証・証拠機構を含む。
2. Skill Magnetとは別の、ユーザー所有スキル保管庫。pack metadata、skill、version、skill固有acceptance checkを保持する。

自動テストはcontract、prompt binding、URL encoding、CLI非起動、fail-closedを検証します。Codex Desktop appの回答を製品から機械取得できない間、handoff受理を回答完了と同一視しません。完成には自動テストに加え、実Desktop taskの依頼と自然文回答の実機証拠が必要です。

## 最小アーキテクチャ

1. Registry/Resolver: ユーザー所有GitHub保管庫、pack ID、目的、完全なcommit SHA、承認、skill固有acceptance checkを検証する。
2. Selection UI: Windowsでは通常右クリックの単一root `Skill Magnet` から日本語の利用者向け名称でskill packを一つ選ぶ。対象AIは次の画面でCodexまたはClaudeを明示選択する。
3. Confirmation UI: 主画面には選択packの表示名、用途、対象project、対象AI、actual requestだけを表示する。pack ID、repository、commit、全skill ID、digest、承認は既定で閉じた「詳細」に格納し、起動の明示確認を取る。
4. Launch contract: UIから共通CLIへ、選択内容、期限、nonce、確認時刻を機械可読形式で渡す。
5. Desktop task prompt: pack ID、全skill ID、検証済みINDEX/SKILL.mdの絶対pathとdigest、actual request、非デモ実行、期待成果、contract/attemptを人が読める形で含める。全instructionをURLへ展開して長さ上限を超えさせず、Codexへ参照ファイルの全文読了とINDEXによる適用部分集合の選定を要求する。未適用skillの受入固定値は要求しない。内部JSONを回答として要求しない。
6. Codex Desktop adapter: `codex://threads/new?path=...&prompt=...` で新規taskへ送る。Codex CLI/TUIは製品targetにしない。
7. Handoff evidence: deep linkをOSへ渡した段階を `desktop_handoff_ready` として記録し、Desktop回答を取得できない限り `verified_completed` を記録しない。
8. Journal/cleanup: source、承認、launch contract、証拠、一時物、期限を記録し、終了・失敗・中断・期限到来後に残留ゼロへ戻す。

GitHubのclone/cacheは正本ではなく、commitから再生成できる検証済み一時物です。

## UIとCLIの分離

共通コアはrepository、選択検証、launch contract、Desktop task prompt、handoff証拠を所有します。OSアダプターはコンテキストメニュー登録、選択project path、確認画面、Codex Desktop protocol handoffを担当します。

WindowsのExplorer→Python/TkはGUI-subsystem launcherが所有します。CodexはOSのDesktop protocol handlerへ直接渡し、Codex CLI、cmd、Windows Terminalを起動しません。ClaudeはWebの新規conversationへprefillし、headless process adapterへfallbackしません。Tkの確認・error dialogは抑止対象ではありません。

| 層 | Windows | macOS | 共通条件 |
| --- | --- | --- | --- |
| 入口 | Explorerコンテキストメニュー | Finderコンテキストメニュー/Quick Action | ユーザーの右クリック操作が必要 |
| 選択 | Explorerでskill pack、画面で対象AI | Skill Magnet画面でskill packと対象AI | pack一つと対象AIを明示選択 |
| 確認 | 共通表示契約 | 共通表示契約 | 対象・版・目的・検証方法を確認 |
| 起動 | OSアダプターからCLI | OSアダプターからCLI | 有効なlaunch contractなしでは拒否 |

メニューの名称、階層、アイコンなどの見た目はOSごとの差を許容します。選択→確認→起動の意味、安全ポリシー、fail-closed判定は同一です。右クリックだけ、自動提案、ログイン、OS起動、repository更新をtriggerにして自動実行してはいけません。

## Codex Desktopの正式ルート

Codexの製品targetはCodex Desktop appです。スキルを常設配置したりCodex CLIへ渡したりせず、固定commitから検証したinstruction全文を新規Desktop taskのpromptへ含めます。

既定候補は次の通りです。

1. Resolverが固定commitの選択packを検証し、pack ID、repository URL、commit SHA、全skill ID、instruction digestを固定する。
2. actual request、検証済みINDEX/SKILL.mdの絶対pathとdigest、非デモ実行、期待成果、contract/attempt、instruction/acceptance digestを自然文promptへ束縛し、参照ファイルの全文読了後、trigger/boundaryと `depends-on` / `composes-with` / `contrasts-with` に従う必要最小集合の選定を要求する。
3. `path`と`prompt`を独立してURL encodeし、`codex://threads/new` の新規taskへ渡す。
4. OSがprotocol handoffを受理した事実とprompt hashを保存する。この状態は `desktop_handoff_ready` であり回答完了ではない。
5. Desktop taskの結果を機械取得できない間は、`verified_completed`、skill適用成功、保存完了を自動生成しない。
6. deep link生成・起動に失敗した場合はerrorを表示し、CLI、既存task resume、ChatGPT webへfallbackしない。

deep linkの受理はモデル挙動や回答完了の保証ではありません。その制約を状態名へ明示し、実Desktop taskの回答確認と分離します。

## Claude

WindowsとmacOSの製品経路は、検証済みpackとactual requestを一つのpromptへ束縛し、`https://claude.ai/new`の新規conversationへprefillします。clipboard、既存conversation、常設plugin、headless `claude --print`へfallbackしません。Web handoffはtask deliveryの境界であり、Claudeが全skillを読み、INDEXに従って適用し、回答を完了した証拠へは昇格させません。CLIのstructured-output adapterは回帰試験用であり、context-menu製品経路から到達させません。

## テスト可能な受入条件

- 起動時のactive packはゼロで、自動提案・自動配布・自動有効化がない。
- ExplorerとFinderの両アダプターで「右クリック→Skill Magnet→skill pack選択→対象AI/用途/依頼内容の確認→起動」が成立する。
- 未確認、期限切れ、改変済み、再利用済みlaunch contractを共通CLIが拒否する。
- OSアダプターが選択外packを指定したり安全判定を迂回できない。
- WindowsとmacOSで同じ入力から同じlaunch contract意味論とfail-closed結果になるcontract testが通る。
- 片方のアダプターが未実装・skip・失敗なら製品完成判定が失敗する。
- ユーザー所有保管庫、完全commit SHA、来歴、承認、acceptance check不足を拒否する。
- Desktop promptへ選択packの全skill、actual request、検証済みINDEX/SKILL.md参照、contract/digest、必要最小集合の選定規則が入り、prompt hashが一致する。
- CLI検証では `evidence.skill_ids` をパック全件の読込対象、`evidence.completed_skill_ids` を実際の適用部分集合として区別し、適用skillだけacceptanceが通り、未適用skillの固定値が `null` である。
- 日本語、改行、空白、`&`、`#`、長文をdeep linkの`path`/`prompt`で損失なく往復できる。
- Codex target pathから `codex exec`、`codex resume`、CLI/TUI、cmd、Terminalを起動しない。
- handoff受理は `desktop_handoff_ready` で、`verified_completed` にならない。
- deep link生成・起動不能、契約不一致、期限切れはfail-closedになる。
- 正常終了、途中失敗、強制終了、期限切れ後に一時物とjournalの残留がない。
- Skill Magnet本体と独立スキル保管庫を使うend-to-endテストが全件成功する。

## 確認した公式資料

- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills) — Codexの明示・暗黙呼び出し、progressive disclosure、repository/user/admin/systemの探索位置。
- 成功比較実装 `C:\Projects\news-obsidian-pipeline` — `codex://threads/new?path=...&prompt=...` の新規task handoff。
- [Claude](https://claude.ai/new) — 製品が新規conversation prefillに用いる固定destination。

過去のCodex CLI検証はDesktop targetの完成証拠には使用しません。
