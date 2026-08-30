# Skill Magnet

Skill Magnetは、ユーザー自身のGitHub保管庫でスキルを保存・版管理し、仕事に必要なスキルパックを選んでCodex Desktop appまたはClaudeへ渡すローカルツールです。

## 現在の状態

GitHub中心の手動activation経路は、スキルパックを一つ選ぶUXです。通常右クリックの正規入口は `Skill Magnet` 一つで、対象パックを選び、確認画面でCodexまたはClaudeと依頼内容を明示します。Codexを選ぶと、CLI/TUIではなくCodex Desktop appの新規taskへ、INDEXで関係づけられたパック内の全スキルと依頼が渡されます。Desktop promptは、確認時のINDEX/SKILL.mdをcontract専用の期限付きmaterializationへ固定し、digestを束縛します。Codexには全ファイルの読了、trigger/boundary・INDEX関係に基づく必要最小集合の選定、最低1つの適用、実依頼の完了を必須化します。skillの説明・一覧・準備確認だけで終了することを禁止します。Skill MagnetはAPI keyや従量課金APIを使わず、既存のCodex DesktopまたはClaude利用プランへhandoffします。Desktop appがdeep linkを受理した時点の製品状態は `desktop_handoff_ready` であり、回答完了を意味しません。Skill MagnetはDesktop回答を取得・検証したとは主張しません。Windows ExplorerとmacOS Finderは規範policy上のsupported adapterです。

旧MVPの `sync` は `~/.agents/skills` と `~/.claude/skills` への常設コピーを前提とし、現在の製品ポリシーに適合しません。CLIでも既定無効です。本番 `sync` は実施していません。

## 製品ポリシー

唯一の規範的な定義は [`policy/product-policy.json`](policy/product-policy.json) です。以下は、その `principles_ja` を読みやすく表示したものです。文書と定義の不一致は自動テストで拒否します。

<!-- product-policy:begin -->
- GitHubのユーザー所有保管庫を唯一の正本とする。
- Skill Magnetはスキルの目的に沿って、必要なパックだけを明示選択して呼び出す。
- Codex/Claudeへの全件・常設・暗黙同期を既定にしない。
- 一時ローカル展開が技術的に必要な場合も、対象・理由・期限・cleanupを明示し、検証後に片付ける。
- 保管庫の版・来歴・承認を保持する。
- ローカル配置の成功を、スキルの読み込み成功または使用成功とみなさない。
- 選択したpackとversionをタスクへ明示し、全skillの読了と最低1つの適用をpromptで必須にする。
- Codex/Claudeの既存利用プランを使い、API key、従量課金API、追加支払いを製品経路で要求しない。
- 公式に確認できる経路または必要な証拠がない場合はfail-closedで停止し、保証外であることを明示する。
- 起動はユーザーの右クリックメニューからの明示選択を条件とし、自動提案・自動配布・自動有効化をしない。
- Windows ExplorerとmacOS Finderで同じ選択・確認・起動の意味と安全ポリシーを提供する。
<!-- product-policy:end -->

このポリシーから、次の既定動作が決まります。

- 起動時に有効なパックはゼロです。
- ユーザーは必要な時にアプリでパックと対象ランタイムを明示選択します。自動提案、自動配布、自動有効化はしません。
- GitHub URL、完全なcommit SHA、承認記録を検証してから有効化します。
- 一時展開物には対象、理由、期限、cleanup方法を記録し、検証後または期限到来時に削除します。
- 全パックの一括配布、ユーザー領域への常設配置、バックグラウンドでの暗黙同期は既定機能にしません。
- ファイルのclone、展開、配置、候補表示だけでは「スキル使用成功」と表示しません。

## 想定する利用手順

現行MVPは、以下の確定済みCLIと右クリック統合でこの流れを実装しています。

1. ユーザー所有GitHub保管庫から利用可能なパックと、その目的・版・承認状態を一覧する。
2. 必要な時にユーザーが右クリックメニューで目的に合うスキルパックを一つ選び、画面でCodexまたはClaudeを明示選択する。
3. `dry-run` で取得元commit、対象、展開場所、期限、cleanup予定、競合を確認する。
4. 選択したpack ID、GitHub URL、commit SHA、全skill ID、instruction digestをタスクへ明示注入する。
5. CodexではDesktop appの新規taskを開き、全skillの読了と最低1つの適用を必須とする一つのpromptを渡す。
6. 利用者はDesktop appまたはClaudeの新規conversationで自然文回答を確認する。Skill Magnetはhandoff成功と回答完了を混同しない。

`dry-run` を通していない有効化は拒否する設計です。

## Windows 11 Quick Start

前提はWindows 11、Python 3.12以降、Visual Studio Build Tools（Desktop development with C++）、Windows 10/11 SDKです。以下はPowerShellで実行します。

1. PowerShellを開き、Skill Magnetのrepository rootへ移動します。

   ```powershell
   git clone --recurse-submodules https://github.com/omusubiman5/skill-magnet.git
   cd skill-magnet
   ```

2. このrepositoryのSkill MagnetをPythonへ登録します。

   ```powershell
   python -m pip wheel . --no-deps --wheel-dir .\dist
   python -m pip install --force-reinstall .\dist\skill_magnet-0.4.0-py3-none-any.whl
   ```

3. Windowsの右クリックメニューを登録します。このcommandはrepository rootで、そのままcopy/pasteできます。

   ```powershell
   python -m skill_magnet install-context-menu --platform windows --confirm
   ```

4. Windowsの確認画面が出た場合は、次節の表と一致するときだけ「はい」を選びます。commandが完了すると、登録結果がJSONで表示されます。

5. Explorerで対象folderそのもの、またはfolder内の何もない場所を通常右クリックします。`その他のオプションを表示`へ進まず、最初のメニューにある`Skill Magnet`から目的に合うskill packを一つ選びます。

6. Skill Magnet画面でCodexまたはClaude、依頼内容を入力し、対象pack、含まれる全skill、用途を確認して実行します。CodexならDesktop appの新規taskが開きます。技術情報は既定で閉じた「詳細」にあります。

メニューを登録しただけではskillやAIを自動実行しません。画面で依頼内容を入力して確認するまで処理は始まりません。

### Windowsの確認画面が出たら

画面には「このアプリがデバイスに変更を加えることを許可しますか？」に相当する文言が表示されます。これはWindows 11の通常右クリックメニューを登録するための確認です。初回導入、modernメニューの再登録・復旧、または削除時の後片付けで必要になる場合があります。Skill Magnetを右クリックから使うたびに出るものではありません。

| 「はい」を押してよい | 「いいえ」を押して停止する |
|---|---|
| 直前に自分で、このREADMEのinstall、rollback、uninstall commandのいずれかを実行した | commandを実行していないのに突然表示された |
| 表示された対象がWindowsの機能で、発行元がMicrosoft WindowsまたはMicrosoft Corporationとして確認できる | 発行元が不明、別会社、または表示内容がREADMEと異なる |
| Skill Magnetの右クリックメニューを登録、復旧、削除しようとしている | 通常のskill実行中に毎回表示された |
| Windowsのaccount policyに従う管理者確認である | Codex/Claudeのpassword、API key、支払い、browser loginを求められた |

この確認はCodex/Claudeの認証、機密入力、外部送信、課金を許可するものではありません。想定外なら拒否し、繰り返し実行せず「トラブル報告に含める情報」を確認してください。

### 正常に導入できた状態

Explorerの通常右クリックに`Skill Magnet`が一つだけ表示されます。`その他のオプションを表示`側に同名のclassic入口が重複していてはいけません。

任意のdirectoryで次のread-only commandを実行します。このcommandはwheelに同梱された既定configを使い、状態を表示するだけで登録を変更しません。導入時と異なるcheckoutのconfigを指定しないでください。

```powershell
python -m skill_magnet context-menu-status --platform windows
```

正常なmodern登録では、出力に少なくとも次の値が含まれます。

```json
{
  "package_registered": true,
  "manifest_contexts": [
    "Directory",
    "Directory\\Background"
  ],
  "command_target_signature_valid": true,
  "self_signed_launcher_referenced": false,
  "deprecated_launcher_exists": false,
  "usable_installed_state": true
}
```

確認するのは、statusの3項目が上記どおりであることと、Explorerの通常右クリックに入口が一つだけあることです。

### 確認画面で「いいえ」を押した／登録に失敗した

UACを拒否しても、作業対象projectのfile、skill内容、Codex/Claude設定を壊しません。登録処理は途中状態を成功扱いにせず、保存した導入前状態へ戻してerrorで停止します。Smart App Controlで遮断され得るclassic fallbackへ切り替えず、自動でUACを承認したり勝手に再試行したりしません。

次の順で再開します。

1. 任意のdirectoryでstatusを確認します。

   ```powershell
   python -m skill_magnet context-menu-status --platform windows
   ```

2. UACの表示内容が前節の「はい」を押してよい条件と一致するか確認します。

3. 一致する場合だけ、同じinstall commandを一度だけ再実行します。

   ```powershell
   python -m skill_magnet install-context-menu --platform windows --confirm
   ```

4. もう一度失敗したら再試行を繰り返さず、statusとerrorを保存して報告します。

### その他のオプションにしか表示されない

`その他のオプションを表示`にだけ`Skill Magnet`がある場合は、旧版のclassic登録が残っている異常状態です。現行版はclassic fallbackを提供しません。旧版の自己署名launcherはSmart App Controlに遮断され得るため、入口が見えても起動可能とは判定しません。

まずstatusを実行し、`usable_installed_state`が`false`であることを確認します。次に、前節の手順どおり同じinstall commandを一度だけ実行してmodern登録を復旧します。復旧後は`usable_installed_state`が`true`になり、通常右クリック側だけに`Skill Magnet`が表示されます。

通常右クリックと`その他のオプションを表示`の両方に`Skill Magnet`がある場合は不具合です。片方を手動で追加・削除せず、再試行を止めて報告してください。

### アンインストールと元に戻す

どちらも任意のdirectoryで実行します。

| 目的 | command |
|---|---|
| 直前の導入・更新を取り消し、保存済みの導入前状態へ戻す | `python -m skill_magnet rollback-context-menu --platform windows --confirm` |
| Skill Magnetの登録を削除する意図を明示する | `python -m skill_magnet uninstall-context-menu --platform windows --confirm` |

現行Windows実装では、更新成功時に直前の導入状態をrollback pointとして入れ替えます。`rollback`はその直前版を復元し、`uninstall`は現在版とrollback point、製品所有の証明書・登録をすべて削除します。初回導入直後の`rollback`は導入前状態へ戻ります。

削除時にWindowsの確認画面が出ることがあります。これは導入時にSkill Magnetが追加したWindowsの信頼情報を片付けるためです。前述の表と一致するときだけ承認します。完了後にstatusを実行し、導入前に登録がなかった環境では`package_registered`と`usable_installed_state`が`false`、Explorerに`Skill Magnet`がないことを確認します。

### よくある質問

**確認画面は毎回出ますか？** いいえ。初回導入、modern再登録・復旧、rollbackやuninstallの後片付けで必要な場合だけです。通常のskill実行で毎回出るなら拒否して報告してください。

**CodexまたはClaudeの認証画面ですか？** いいえ。Windowsの右クリックメニュー登録に対する確認です。AIのpasswordやAPI keyを入力しません。

**外部送信を許可する画面ですか？** いいえ。この確認だけで依頼内容やfileを外部へ送りません。

**発行元が違う場合は？** 「いいえ」を選びます。別の実行fileや不明な発行元を承認しないでください。

**CLI画面が残る場合は？** Skill Magnetの確認・結果・error画面が開いていれば先に閉じます。製品画面を閉じても新しいcmd/Terminalが残る場合は、他のterminalを終了せず、時刻とerrorを記録して報告してください。

**二重に表示される場合は？** 正常ではありません。両方に入口があることを文章で記録し、必要ならメニュー部分だけを切り取った画像とstatus出力を添えてください。手動でregistryを編集しないでください。

### トラブル報告に含める情報

- `context-menu-status`の出力
- 発生時刻とtimezone
- 通常右クリック、または`その他のオプションを表示`のどちらを選んだか
- 表示されたerror文を省略せず、そのまま文字で転記したもの
- install、rollback、uninstallのどのcommandを直前に実行したか

secret、API key、password、認証fileの内容は含めないでください。desktop全体や他applicationを含む画面全体も貼らず、必要ならSkill MagnetまたはUACの範囲だけを切り取ります。

<details>
<summary>運用者向け: Windows登録の内部詳細</summary>

modernメニューは署名済みMSIX identity packageとExplorer用COM commandを登録します。初回または証明書が未登録のとき、Windows標準の`certutil.exe`を昇格起動し、`Skill Magnet Local`証明書をmachineの`TrustedPeople`へ登録します。cleanupではSkill Magnetが作成した証明書だけを削除します。

installは単一transactionでrollback pointを作り、modernがusableならclassic rootを削除します。modern登録に失敗した場合は部分登録を除去し、rollback pointを復元してerrorで停止します。Explorer用COM DLL、固定メニューmanifest、`SkillMagnetIdentity.exe`はすべてfull MSIXへ収容します。COM DLLはメニューmanifestに固定したAuthenticode-validなPythonを`CREATE_NO_WINDOW`で直接起動します。自己署名のprocess adapterは実行経路に置かず、`SkillMagnetIdentity.exe`はidentity anchor専用でメニュー選択時には実行されません。

</details>

packの追加・削除・版・含有skillを変更した後は、同じinstall commandを一度だけ実行して静的メニューを更新します。古いメニューはcommitまたはskill集合の不一致でfail-closedになります。Skill Magnet画面のCancelまたはcloseではcontract、evidence、stateを作成しません。

## macOS Quick Start

macOSではFinder Quick Actionとして登録・解除します。

```bash
python -m skill_magnet --config /path/to/skill-magnet.json install-context-menu --platform macos --confirm
python -m skill_magnet --config /path/to/skill-magnet.json uninstall-context-menu --platform macos --confirm
```

実packを変更せず事前検証するには `activation-plan` を使います。

```powershell
python -m skill_magnet activation-plan --platform windows --project C:\path\to\target --pack codex-delivery-assurance --purpose "このタスクの目的"
```

保管庫契約は [`docs/skill-repository-contract.md`](docs/skill-repository-contract.md) を参照してください。

## 実行先の現状

- Claude: WindowsとmacOSの製品経路は、検証済みpackと依頼を一つのpromptに束縛し、`https://claude.ai/new`の新規conversationへprefillします。clipboard、既存conversation、常設plugin、headless `claude --print`へfallbackしません。handoffは回答完了を意味しません。
- Codex: `codex://threads/new?path=...&prompt=...` を使い、Codex Desktop appの新規taskへhandoffします。`path`と`prompt`は別々にURL encodeし、日本語、改行、空白、`&`、`#`を保持します。Skill MagnetのCodex実行先として `codex exec`、`codex resume`、CLI/TUI、cmd、Windows Terminalは起動しません。

Codex Desktopのpromptには、選択pack ID、全skill ID、contract専用materialization内のINDEX/SKILL.mdの絶対pathとdigest、actual request、非デモ実行の指示、期待成果、contract/attemptを人が読める形で含めます。Codexには参照ファイルの全文読了、最低1つの適用、INDEX関係の遵守、実依頼への直接回答を要求し、説明・一覧・準備確認だけで終了することを禁止します。API key、従量課金API、追加支払いは要求しません。deep linkをOSへ渡した時点は `desktop_handoff_ready` です。completion receipt、callback command、Desktop output schemaは作らず、`handoff_completed: true`、`answer_completion_claimed: false`として記録します。詳細は [`docs/mvp-redesign.md`](docs/mvp-redesign.md) にあります。

旧CLI verification adapterは回帰試験用コードとして残っていますが、Codex Desktop製品経路からは到達しません。global/user Codex configは変更しません。Desktop app自身の設定と認証はDesktop appが所有します。

## 成果物と完了条件

成果物は、Skill Magnet本体と、そこから独立したユーザー所有のスキル保管庫です。MVPの目的は、保管庫の固定commitから一つのpackageを選び、INDEXと全skillをCodex Desktop appの新規taskへ依頼と一体で渡し、タスク内で必要なskillだけを適用して回答を得ることです。

この一連を両方の成果物を使ったend-to-end自動テストで合格した時だけ完成とします。ローカル配置、候補表示、旧syncテストの成功だけでは完成ではありません。

## テスト

現段階のテストは二種類あります。

- Desktop handoff E2E: 独立Git保管庫、期限付きcontract、選択pack、actual request、INDEX/SKILL.md digest、skill適用必須prompt、deep-link encoding、`desktop_handoff_ready`、回答完了を主張しない状態、起動失敗cleanupを検証します。
- 製品ポリシーテスト: 規範的定義が必須制約を保持し、READMEと設計文書の表示が一致することを検証します。
- 旧MVPテスト: 旧常設syncエンジンの安全性を回帰確認します。成功しても、再設計後MVPの完成を意味しません。

```powershell
python -m unittest discover -s tests -v
```

再設計後MVPの完了条件は、別保管庫の固定commitからの単一pack選択、全skillの読込とINDEXに基づく最低1つのskill適用を必須化したprompt、Desktop新規taskへの正確なbinding、API key／従量課金APIを使わないhandoff、Windows ExplorerとmacOS Finderの両実入口を確認することです。handoffは回答完了として表示せず、どちらか一方のadapterだけの成功を完成扱いにしません。

GitHub ActionsはWindowsとmacOSの両jobを必須の同一テストsuiteとして定義しています。片方だけの成功を完成扱いにしません。

過去に実施したCodex CLI runtime acceptanceは、CLI adapter自体の回帰資料です。Codex Desktop appを実行先とする現製品の完成証拠には転用しません。
