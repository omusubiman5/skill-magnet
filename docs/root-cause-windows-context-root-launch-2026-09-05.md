# Windows右クリック「Skill Magnet」が起動しない原因調査

## 結論

不具合は二段階で確認された。

1. 0.5.8では`Skill Magnet` rootの`command_`が空で、rootの`Invoke`はログを書き込む前に`E_NOTIMPL`を返していた。rootは`ECF_HASSUBCOMMANDS`だけを持つ子メニューのcontainerであり、root自体からアプリを起動できなかった。
2. 0.5.9の最初の修正では、rootへcommandを追加して`ECF_HASSUBCOMMANDS | ECF_HASSPLITBUTTON`にした。しかし、実Windows Explorerはこれを押せるrootと子メニューのsplit buttonとして動作させず、従来どおりflyoutとして扱った。package COMは起動したがrootの`Invoke`には到達せず、GUIも開かなかった。

したがってsplit-button remediationは不採用とする。最終設計は、**Explorerには子項目を一件も出さず、単一の`Skill Magnet` rootを直接実行する。skill／pack選択、Library Manager、右クリックfolderの登録は、起動後の統合GUIに置く**である。

最上位の根本原因は、Windows Explorerの実際の表示・dispatchを現行版で確認せず、`IExplorerCommand`のflag、DLL単体試験、package status、process生成をExplorerからの起動成功へ一般化したことである。

### 追加実測：AppxとPython runtimeが別世代だった

2026-09-05の導入済み状態を独立に読み取ると、次の不一致が確認された。

| 面 | 実測値 |
|---|---|
| 登録済みAppx package | `0.5.9.0` |
| `python -I -c "import skill_magnet; print(skill_magnet.__version__)"` | `0.5.0` |
| `importlib.metadata.version("skill-magnet")` | `0.5.9` |

これは「0.5.9 packageが登録済み」「distribution metadataが0.5.9」という二つの正常表示だけでは、Explorer commandが実際にimportする`skill_magnet`本文まで0.5.9であることを証明できない、split-generationの実績である。ローカルの単体試験もimport元を固定しなければ、この旧`site-packages`本文を読んで現行sourceの試験と誤認する。

したがって、登録済みAppx、menu commandが指すPython executable、importされたmoduleの`__version__`と絶対path、distribution metadataのversionと所有file、runtime treeのdigestを一つの証拠鎖で照合しなければならない。どれか一つでも不一致なら、package statusが正常でも実機受入は失敗とする。

## 確認済みの事実

### 0.5.8の事象

| 証拠 | 確認できること | 確認できないこと |
|---|---|---|
| 2026-09-04 23:56の利用者画面 | Windows 11の通常右クリックに`Skill Magnet` rootが表示された | rootまたは子項目の`Invoke`が成功したこと |
| AppModel Runtime 210/211 | 23:56:13に0.5.8 package COM containerが作成され、PID 24212が追加された | Python processまたはGUIが起動したこと |
| AppModel Runtime 217 | 23:56:27にCOM containerが破棄された | 利用者操作が完了したこと |
| `%LOCALAPPDATA%\SkillMagnet\ContextMenu\invoke.log` | 最終更新は同日09:42で、23:56の操作による記録はなかった | 23:56にleaf commandが実行されたこと |
| 0.5.8 native実装 | rootの`command_`は空、空なら`Invoke`は`E_NOTIMPL`、root flagは`ECF_HASSUBCOMMANDS`だけだった | rootを押して起動できること |

AppModelのeventは「Explorerが拡張を読み、メニューを列挙した」証拠である。`invoke.log`が更新されていないため、起動成功の証拠にはならない。rootの旧実装を直接押した場合は、空commandによる`E_NOTIMPL`と観測結果が一致する。

### split-button remediationの実Explorer却下

0.5.9の最初の候補では、空rootを次の構成へ変更した。

- rootに直接launcher commandを設定。
- root flagを`ECF_HASSUBCOMMANDS | ECF_HASSPLITBUTTON`に設定。
- root配下に、folder登録、Library Manager、2 pack、1 skillの合計5子項目を保持。
- installed menu manifestはroot launcherを含む6 actionだった。
- `context-menu-status`は`menu_contract_valid: true`、`menu_contract_matches_config: true`、`usable_installed_state: true`を返した。

しかし実Explorerでは次の結果だった。

| 時刻 | 実測 |
|---|---|
| 2026-09-05 00:22 | split-button候補の0.5.9 packageと6-action manifestを導入 |
| 00:23:19 | AppModel Runtime 210/211。0.5.9 COM containerへPID 8480を追加 |
| 00:24:43 | AppModel Runtime 217。上記containerを破棄 |
| 00:25:16 | 別の実操作で0.5.9 COM containerへPID 10356を追加 |
| 00:25以降 | `invoke.log`は2026-09-04 09:42のまま。`invoke_enter`、selection、CreateProcessの現行記録なし |

つまり、Explorerはpackageを読み込んだが、root launcherの`Invoke`を呼ばなかった。`ECF_HASSPLITBUTTON`を付けても、`ECF_HASSUBCOMMANDS`を持つrootは実Explorerで信頼できる直接起動面にならなかった。statusとDLL直接試験が正常でも、この実測を覆せない。

この結果により、次の案を明示的に却下する。

- root label clickで統合GUIを開き、矢印から子項目を選ばせるsplit button。
- `Skill Magnet`配下にLibrary Manager、folder登録、skill／packを並べるflyout。
- 子項目が残る限りroot clickも動く、というflag依存の設計。

## 失敗ツリー

```text
利用者が右クリックのSkill Magnetからアプリを起動できない【実績】
├─ A. 0.5.8 rootに起動actionがない【実績・直接原因】
│  ├─ root.command_が空【実績】
│  ├─ 空commandのInvokeがログ前にE_NOTIMPLを返す【実績】
│  └─ GetFlagsがECF_HASSUBCOMMANDSだけを返す【実績】
├─ B. split-button修正でもroot Invokeへ到達しない【実績】
│  ├─ rootへcommandを設定した【実績】
│  ├─ ECF_HASSUBCOMMANDSとECF_HASSPLITBUTTONを併用した【実績】
│  ├─ installed manifestに5子項目を残した【実績】
│  ├─ 実Explorerはpackage COMを起動した【実績】
│  └─ 現行invoke logとGUI表示が発生しなかった【実績】
│     └─ flag上のsplit表現をExplorer実挙動と同一視した【実績】
├─ C. Explorer背景では対象folderを確定できない経路がある【実装確認済み】
│  ├─ AppxManifestはDirectory\Backgroundを対象とする【実績】
│  ├─ 背景InvokeではIShellItemArrayがnullになり得る【潜在】
│  ├─ 旧SelectedPathはnullまたは1件以外をE_INVALIDARGにした【実績】
│  └─ IObjectWithSiteから現在folderを得るfallbackがなかった【実績】
├─ D. nativeが起動processの直後終了を成功扱いする【実績】
│  ├─ CreateProcess成功直後にhandleを閉じた【実績】
│  ├─ processのexit codeを観測しなかった【実績】
│  └─ Pythonがexit 2でも「process生成成功」しか残らない【潜在】
├─ E. Python起動前errorが利用者に届かない【実績】
│  ├─ Config.loadと中断復旧はcontext UIのtryより前に走る【実績】
│  ├─ contextのouter catchはstderrを抑止して2を返す【実績】
│  └─ 利用者には原因・復旧操作・診断場所が表示されない【実績】
├─ F. 永続メニューが一時的なworktreeへ依存する【実績】
│  ├─ commandにsource rootの絶対pathを埋め込む【実績】
│  ├─ menu登録はworktreeの寿命より長い【実績】
│  └─ worktree移動・削除後にbootstrapが起動前停止する【潜在】
├─ G. 登録済みAppxと実際にimportされるPython本文が別世代になる【実績】
│  ├─ Appx versionは0.5.9.0【実績】
│  ├─ distribution metadataは0.5.9【実績】
│  ├─ 同じinstalled Pythonのmodule __version__は0.5.0【実績】
│  └─ package登録確認だけではruntime payloadを拘束しない【実績】
└─ H. リリースゲートがA〜Gを検出しない【実績・根本原因】
   ├─ 製品installとWindows CIが-SkipContractTestを渡す【実績】
   ├─ C++ contract testはDLLを直接LoadLibraryする【実績】
   │  ├─ Explorer/package COM surrogateを通らない【実績】
   │  ├─ 選択itemを試験側で人工的に1件作る【実績】
   │  └─ Explorerがrootをflyoutとしてdispatchする挙動を再現しない【実績】
   ├─ fixtureとC++期待値がstaleでもskipによりreleaseを止めない【実績】
   ├─ CreateProcess成功をGUI起動成功とする【実績】
   └─ 0.5.8台帳が0.5.1のExplorer実機証拠を受理する【実績】
      └─ 現行版の実root Invokeを確認せず完了表示できる【実績】
```

## 原因の階層

### 直接原因

- 0.5.8: rootに実行commandがなく、root clickの`Invoke`が`E_NOTIMPL`だった。
- 最初の0.5.9: root commandを追加しても、子項目を持つrootは実Explorerで直接起動されなかった。

### 経路原因

- `Directory\Background`を宣言しながら、選択配列がない時の現在folder取得を実装していなかった。
- native process生成後の即時異常終了を観測していなかった。
- Config読込や中断復旧の失敗を、一つの利用者向けUIへ変換していなかった。
- Explorerへ保存するcommandが、削除され得るsource worktreeを参照していた。
- 子メニューへ機能を分散し、Explorerのdispatch方式を製品の必須経路にした。

### 根本原因

「packageが登録されrootが表示される」「COMが活性化する」「DLL単体からrootをInvokeできる」「CreateProcessがtrueを返す」を上位の証拠へ一般化し、次の連鎖をcurrent releaseの実Explorerで検証しなかった。

```text
Explorer実右クリック
→ 子を持たないSkill Magnet rootを押す
→ IExplorerCommand::Invoke
→ 対象folder確定
→ installed Python module起動
→ module version・distribution metadata・runtime payloadが同じwheelと一致
→ 設定・中断状態検証
→ 統合GUI表示
→ skill／pack選択・Library Manager・folder登録
```

## なぜ利用者が復旧できなかったか

| 失敗位置 | 利用者に見えたもの | 欠落していた復旧情報 |
|---|---|---|
| 空root command | 何も起きない | rootが実行項目ではないこと、次に押す場所 |
| split-button未dispatch | flyoutだけが表示される、または何も開かない | root Invokeが発生していない事実、代替入口 |
| 背景folder解決 | 何も起きない、または汎用error | 対象folderの選び直し方 |
| process即時終了 | native側はprocess生成成功 | exit code、診断ログ、再試行前に直す項目 |
| config／中断復旧 | consoleを出さずexit 2 | 実際の原因、統合GUIまたはCLIでの復旧操作 |
| stale worktree | Python UIより前に停止 | 永続commandの修復・再登録方法 |

「安全確認を満たせませんでした」のように原因を隠す汎用文と、「原因を解消して再実行」のようにアプリ内で実行できない指示は復旧手段ではない。最終0.5.9は、実原因、操作場所、再試行条件、診断ログを同じerror surfaceに表示する必要がある。

## 最終設計を直接root一件にする理由

- `EnumSubCommands`を`E_NOTIMPL`にすれば、Explorerにflyoutとして扱わせる要因を製品側から除去できる。
- rootは`ECF_DEFAULT`の一つの実行項目となり、利用者の「Skill Magnetを押せば起動する」という理解と一致する。
- skill／pack、管理、登録の選択を統合GUIに移すことで、Explorer固有の子command dispatchへ中核機能を依存させない。
- GUIで処理中表示、error、復旧、多重投入制御を一貫して提供できる。
- menu shapeは登録済みskill数で変化せず、skill追加のたびにExplorer子項目を再構築する必要がない。

## リリース判定

最終0.5.9のコードと自動試験が通っても、0.5.9 wheelを再導入してsplit-generationを解消し、実Explorerで背景folderと選択folderの両方から単一rootを押し、統合GUI、多重投入制御、具体的なerror／復旧経路まで確認するまではリリース可と判定しない。package status、COM activation、DLL直接load、distribution metadataだけのversion一致、旧0.5.1のfield evidenceは代替証拠にしない。

同一buildの証明には、repositoryの固定native入力から再計算したsource tree digest、`SkillMagnetNativeSource.json`、DLL export／埋込binding、signed MSIX内payload、登録済みpackage root、外部install rootの一致も含める。Appx／Python runtimeだけが一致してもnative DLLが別sourceなら失敗とする。またrollbackは、所有path、metadata、registry hash、package identity、外部file manifestの完全性をuninstall・削除より前に検証し、壊れたsnapshotでは現在状態を変更しない。最新の実Explorer受入結果、test件数、release commit、wheel digestは[Windows Explorer release evidence](windows-explorer-leaf-launch-results.md)のmachine-readable ledgerだけを正本とし、原因調査書へ途中状態を複製しない。
