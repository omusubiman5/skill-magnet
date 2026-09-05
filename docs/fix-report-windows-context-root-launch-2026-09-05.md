# Windows右クリック「Skill Magnet」が起動しない問題 修正対応報告

## 最終方針

0.5.9のWindows Explorer入口は、**子項目を一件も持たない単一の`Skill Magnet` root**とする。`Skill Magnet`を押すと統合GUIを直接開く。

次の操作はExplorerの子メニューから撤去し、統合GUIの中へ移す。

- `Skill Pack: <表示名>`または`Skill: <表示名>`の選択。
- Codex／Claudeの選択と依頼内容の入力。
- `Library Manager`。
- `このフォルダーのスキルを登録`。

```text
Explorerでfolderまたはfolder背景を右クリック
└─ Skill Magnet                 ← 子なし、直接Invoke
   └─ 統合GUI
      ├─ Skill Pack / Skillを選ぶ
      ├─ Codex / Claudeと依頼内容を選ぶ
      ├─ Library Manager
      └─ このフォルダーのスキルを登録
```

## split-button案を採用しない理由

最初の0.5.9候補では、rootにlauncher commandを追加し、`ECF_HASSUBCOMMANDS | ECF_HASSPLITBUTTON`を返した。Explorer配下にはfolder登録、Library Manager、2 pack、1 skillの5子項目を残した。

この候補は次の機械検査を通る状態だった。

- installed manifestにroot launcher 1件と子action 5件が存在。
- package、DLL、menu contract、configが一致。
- `usable_installed_state: true`。
- 0.5.9 package COM containerが実Explorer操作で起動。

それでも実Explorerではroot launcherの`Invoke`が記録されず、GUIが開かなかった。2026-09-05 00:23:19と00:25:16に0.5.9 COM processは作成されたが、`invoke.log`は前日09:42から更新されなかった。

したがって、split-button flagを根拠に「root labelは起動、矢印は子メニュー」という操作を保証する案は却下した。子commandは0件にし、Explorerへ一つの直接commandだけを渡す。

## 0.5.9の最終修正内容

| 修正 | 最終実装 | 防ぐ失敗 |
|---|---|---|
| 直接root launcher | menu contract v4を`launcher` record正確に1件だけへ変更する。rootへcommandを設定し、`GetFlags`は`ECF_DEFAULT`、`EnumSubCommands`は`E_NOTIMPL`を返す | rootがflyoutになり`Invoke`されない |
| 統合GUI | root launcherは`context --platform windows --launcher`を起動する。現在のconfigからskill／packを表示し、Codex／Claude、依頼内容、Library Manager、folder登録を同じ画面に置く | Explorer子項目へ機能が分散し、一部だけ起動できない |
| Explorer背景folder fallback | `MenuNode`へ`IObjectWithSite`を実装する。選択配列がない場合はsiteの`IServiceProvider`から`SID_SFolderView`を取得し、`IFolderView::GetFolder`で得た`IPersistFolder2::GetCurFolder`をfilesystem pathへ変換する | `Directory\Background`で`IShellItemArray == null`となり起動できない |
| 起動processの即時終了検出 | `CreateProcessW`後に1,200msだけprocessを観測する。非0で即時終了した場合は`child_process_failed`、exit code、復旧操作、`invoke.log`場所を表示して失敗を返す。継続中は`child_running`を記録する | Python exit 2をnative成功と誤認する |
| 起動前error UI | Config読込、中断attempt復旧、選択検証、handoffのどこで失敗しても、context入口は実原因と次の操作を一回だけ表示する | consoleを出さず何も起きない、汎用文だけで復旧不能になる |
| installed-module bootstrap | 永続menu commandを`python -I -m skill_magnet --config <absolute-config>`へ変更し、`sys.path.insert`とsource worktree絶対pathを廃止する | worktree移動・削除後に既登録menuが停止する |
| installed runtime世代拘束 | field collectorはAppx version、module `__version__`、distribution metadata version、import済みmodule pathとdistribution所有path、runtime tree digestを同じmenu executableから取得する。gateは全version一致、path一致、runtime digestとrelease wheel/sourceの一致を必須にする | Appx 0.5.9.0／metadata 0.5.9でもmodule本文だけ0.5.0のsplit-generationを正常扱いする |
| native source／artifact拘束 | 固定native入力のsource tree digestを`SkillMagnetNativeSource.json`へ記録し、DLL export／埋込markerへ一意にbindingする。field gateはsigned MSIX、登録済みpackage root、外部install rootのmanifest／DLL／identity payload一致を要求する | sourceと異なるDLL、再hashしたmanifest、別buildのMSIXを同世代と誤認する |
| rollback破壊前検証 | rollback snapshotの所有path、schema、registry SHA-256、package identity、外部file manifest、予期しないentry不在を、uninstall／削除より前に全件検証する。不一致時は現在状態を変更せず停止する | 壊れたbackupを信じて正常な現行installを先に削除する |
| 多重投入制御 | root起動から処理完了まで同じstateをOS file lockでsingle-flight化する。同一folderの再投入を重複処理せず、別folderの並行投入は現在の処理と再試行方法を表示する。異常終了時はOSがlockを解放する | 画面が開くまでの連続右クリック、二重登録、回復不能な残存lock |
| native contract testの必須化 | 製品installとWindows CIから`-SkipContractTest`を除去する。rootが有効、flagが`ECF_DEFAULT`、subcommandが0件、選択folder／背景folderのroot Invoke、即時exit 0／7を検証する | stale fixtureやInvoke未試験のbuildが通過する |
| 現行版のfield evidence gate | release台帳のWindows Explorer証拠を現行versionへ一致させ、現行`invoke.log`のSHA-256を必須にする | 0.5.1の実機結果を0.5.8／0.5.9へ転用する |

## 正常なmenu contract

最終0.5.9のstatusは、少なくとも次の形でなければならない。

```json
{
  "menu_leaf_count": 0,
  "menu_action_count": 1,
  "root_launcher_entry_count": 1,
  "library_manager_entry_count": 0,
  "register_folder_entry_count": 0,
  "native_source_manifest_valid": true,
  "native_artifact_hashes_valid": true,
  "dll_native_source_binding_valid": true,
  "native_build_binding_valid": true,
  "usable_installed_state": true
}
```

登録済みskill／packの数は`configured_selection_count`としてconfigから確認する。Explorer action数へ加算しない。通常右クリックの`Skill Magnet`には矢印がなく、押すと統合GUIが直接開く。

## 導入済みruntimeの世代一致

修正中の実測では、Appxは`0.5.9.0`、distribution metadataは`0.5.9`である一方、同じinstalled Pythonでimportした`skill_magnet.__version__`は`0.5.0`だった。この状態は未修復であり、0.5.9 wheelの最終再導入と実機証拠取得が必要である。

合格条件は、単なるversion文字列の一致ではない。次を同時に満たす必要がある。

1. Appx release、module `__version__`、distribution metadata versionが同じreleaseを示す。
2. importされた`skill_magnet/__init__.py`が、そのdistributionのfile一覧に含まれる同じ絶対pathである。
3. import先の`skill_magnet` runtime tree digestが、検査対象wheel内のruntime tree digestおよび同じrelease sourceから得たdigestと一致する。
4. menu manifestのPython executableが、このprobeを実行したexecutableと一致する。

この照合に加え、native source manifest、DLL binding、signed MSIX／package root／external rootのpayload照合を通過しない既存導入物は、再登録やfield evidence取得へ進めない。再導入後に同じprobeを再実行し、旧0.5.0本文や別buildのnative artifactが残っていないことを確認する。

## 利用者が自分で復旧できる経路

### 1. 統合GUIが開いた後に失敗した

- 設定・JSON・menu不一致: 同じ画面の`Library Manager`を開き、GitHub URLと登録内容を確認して`Skill Magnetへ反映`を実行する。
- 中断transaction: `Library Manager`に表示される`続きから再開`または`最初からやり直す`を選ぶ。
- folder登録error: 表示された不足fileまたは構造errorを直し、同じ画面の`このフォルダーのスキルを登録`を一回だけ再実行する。
- skill実行error: 実原因、未実行範囲、次の操作を画面で確認してから再試行する。

error UIは「原因を解消してください」だけで終わらず、実際の例外文、操作する画面、未実行範囲、再試行条件を表示する。

### 2. `Skill Magnet`を押しても統合GUIが開かない

native errorが表示された場合は、表示されたWindows errorまたはexit codeと`%LOCALAPPDATA%\SkillMagnet\ContextMenu\invoke.log`を保存する。folder特定errorなら、対象folderそのものを一件だけ選択して右クリックするか、対象folderを開いて余白を右クリックして一回だけ再試行する。

何も表示されない場合はread-only statusを取得する。`<config>`はmenu登録時に使用した`skill-magnet.json`の絶対pathへ置き換える。

```powershell
python -I -m skill_magnet --config "<config>" context-menu-status --platform windows
```

`usable_installed_state`または`native_build_binding_valid`が`false`、`menu_action_count`が1以外、または`menu_leaf_count`が0以外なら、pack／skill変更による再登録は行わず、同じ0.5.9のinstalled module、native source、MSIX、config locationを揃えた明示的repairとしてmenuを一度だけ再登録する。

```powershell
python -I -m skill_magnet --config "<config>" install-context-menu --platform windows --confirm
```

Python module自体を読み込めない場合は、registryやAppxを手作業で削除せず、同じ0.5.9配布物を再導入してからstatusを実行する。再登録に再度失敗した場合は連続再試行せず、次を保存する。

- status全文。
- 表示された実原因とexit code。
- `%LOCALAPPDATA%\SkillMagnet\ContextMenu\invoke.log`。
- 発生時刻とtimezone。
- folderそのものを右クリックしたか、folder背景を右クリックしたか。
- 連続投入した場合は回数と間隔。

### 3. 連続右クリックした

- 同一folderの2回目以降は新しい処理を開始しない。
- 既存GUIが動作中なら、その処理中状態を表示する。
- 別folderの要求なら、現在処理中のfolderと、完了後に再試行することを表示する。
- アプリを誤って閉じた場合は、OS lock解放後に同じfolderから再起動できる。lock fileが残っているだけで永久拒否しない。

## 自動検証で必須にした範囲

以下は最終0.5.9 release候補で実行すべき自動検証であり、本書作成時点では未実行のPASSを記載しない。

| 検証 | 合格条件 |
|---|---|
| menu contract | v4にroot launcherが正確に1件、package／skill／manager／registerのExplorer child recordが0件 |
| root COM contract | root stateがenabled、flagが`ECF_DEFAULT`、`EnumSubCommands`が`E_NOTIMPL` |
| 選択folder Invoke | 一件の選択folderからpathを得てroot commandを起動する |
| 背景folder Invoke | null selectionとsite folder viewからpathを得てroot commandを起動する |
| 即時exit | exit 0を正常終了、exit 7を`child_process_failed`として検出する |
| 統合GUI | skill／pack選択、Codex／Claude、依頼内容、Library Manager、folder登録が一画面から到達可能 |
| pre-UI failure | Config読込・中断復旧・選択検証の失敗ごとに、具体的で復旧可能なUIを一回だけ出す |
| 多重投入 | 同一folder重複、別folder並行、holder異常終了後の再取得を区別する |
| bootstrap | commandにworktree pathと`sys.path.insert`がなく、`-I -m skill_magnet`を使う |
| installed runtime identity | Appx、module、distributionのversionが一致し、module pathがdistribution所有fileと一致し、installed runtime digestが検査対象wheel／release sourceと一致する |
| native artifact identity | repository native入力、source manifest、DLL export／埋込binding、signed MSIX、package root、external rootのdigestとbytesが一致する |
| rollback safety | snapshotの全metadata／hash／所有pathを破壊的操作前に検証し、改ざん・欠落・余分なentryでは現行状態を不変にする |
| install lifecycle | `-SkipContractTest`なしでnative build、MSIX install、status、rollback、uninstallが完走する |
| field ledger gate | 現行version固有statusと64桁の現行invoke log SHA-256がない限り失敗する |

## Explorer実機受入

本書作成時点の状態は**PENDING**である。split-button候補が実Explorerで失敗した以上、コード差分、自動test、package status、COM activationを実機PASSへ読み替えない。

最終0.5.9の完了には、実Windows Explorerで次の5群をすべて確認し、同じ導入物から得た証拠を台帳へ固定する必要がある。

### 1. 背景folder／選択folder双方のroot Invoke

- folderそのものを一件選択して通常右クリックし、子を持たない`Skill Magnet`を押す。
- folderを開いた余白で通常右クリックし、同じ`Skill Magnet`を押す。
- 双方で現行0.5.9の`invoke_enter`、`selection_succeeded`、process起動結果が`invoke.log`へ記録される。

### 2. 統合GUI表示

- 両経路で統合GUIが前面に開く。
- 右クリックしたfolderが作業対象として一致する。
- Explorer側に矢印と子項目が表示されない。

### 3. 統合GUI内の全入口

- `Skill Pack: ...`または`Skill: ...`を選べる。
- Codex／Claudeと依頼内容を入力・確認できる。
- `Library Manager`を開ける。
- `このフォルダーのスキルを登録`へ、右クリックしたfolderを選び直さず渡せる。

### 4. 失敗時の具体原因と復旧手段

- 存在しないconfig、folder解決失敗、即時非0終了、中断transactionをそれぞれ発生させる。
- 各errorが実原因、未実行範囲、利用者が押す場所または実行するcommand、再試行条件、診断log場所を表示する。
- 汎用文だけ、無表示、exit codeだけで終了しない。

### 5. 多重投入制御

- 同一folderから短時間に連続投入してもGUIと処理を二重作成しない。
- 別folderを並行投入した場合は現在処理中の要求と再試行方法を表示する。
- GUIを途中で閉じた後、OS lockが解放され、同じfolderから利用者自身で再起動できる。

上記のfield evidenceには、package full name、installed menu manifest SHA-256、DLL SHA-256、config SHA-256、invoke log SHA-256、実施時刻、背景／選択folderの区別に加え、module version、distribution version、module／distribution所有path digest、installed runtime tree digest、native source manifestとsource tree digest、DLL export／埋込binding、signed MSIX／package root／external rootのpayload一致を含める。旧0.5.1のchild menu証拠、失敗したsplit-button候補、別buildのstatusは流用しない。

## 現在の完了判定

| 層 | 状態 |
|---|---|
| 原因特定 | 完了。空rootに加え、split-buttonが実Explorerでroot Invokeされないことまで特定した |
| split-button案 | **却下**。子項目を残さない |
| 最終0.5.9設計 | 子0件、単一root直接起動、全操作を統合GUIへ集約 |
| 自動test | 本書には未実行のPASSを記録しない。最終release実行結果を別途固定する |
| 導入済みruntime | **FAIL**。Appx 0.5.9.0／metadata 0.5.9に対しmodule本文が0.5.0。最終wheel再導入と同一性再確認が必要 |
| 実Windows Explorer | **PENDING**。runtime世代一致後に5群の現行版field evidenceが必要 |
| リリース可否 | **未承認**。導入済みruntime修復、実機受入、current-version field evidence gate通過後に判定する |
