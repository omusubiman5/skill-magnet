# Skill Magnet 0.3.0 release remediation implementation report

日付: 2026-08-29  
対象: `C:\Projects\skill-magnet`  
計画: [`release-remediation-implementation-plan-2026-08-29.md`](release-remediation-implementation-plan-2026-08-29.md)

## 結論

NO-GOレビューでコード修正対象となった、壊れたwheel、固定project path、証明書ownership消失、旧自己署名trust残留、rollback異常残留、Desktop入力TOCTOU、削除された結果gate、旧個別leaf文書、版不整合、CIのsubmodule/native欠落を修正した。0.3.0 wheelからの隔離install、Windows native build、MSIX署名、実Appx update/installまで成功し、現在の実機は`SkillMagnet.ContextMenu_0.3.0.0_x64`のusable状態である。

証明書cleanup実装基準commit `668d45b`はremote Windows/macOS CIがgreenで、Windows CI内の旧証明書upgrade cleanup、実MSIX install/rollback/uninstallと残留ゼロ、macOS CI内の実Quick Action install/Automator実行/uninstall/残留ゼロも完走した。cleanup証拠をCLIへ保持する修正は`76aad58`。Windows 0.3.0実機ではExplorerから単一`PMO`を選び、Codex Desktop handoffまで確認した。ローカル導入版の判定は、新規Desktop taskの自然文回答を確認するまで`CONDITIONAL NO-GO`とする。handoff受理を実回答完了へ読み替えない。公開Store配布は正式publisher identityがないため別途`NO-GO`である。

## 実装内容

### 配布artifact

- `setup.py`のbuild hookでWindows native source、既定config、固定commit `c7747bba…`の9-skill packをwheelへ収録した。
- bundled snapshotはrepo URL、commit、INDEX digest、各skill directory digestを実行時検証する。Git metadataがないwheel内でも改変をfail-closedにする。
- `package://codex-pmo-skills-c7747bba`を導入し、source checkoutではsubmodule、wheelではbundled snapshotへ解決する。
- wheelの既定configとinstalled `site-packages`を使うため、生成されたExplorer commandから`C:\Projects\skill-magnet`固定参照を除去した。
- Python版を`0.3.0`、MSIX版を`0.3.0.0`へ同期した。

### 証明書・rollback・uninstall

- 同一thumbprintの更新では`created_my`、`created_trusted_people`、`created_machine_trusted_people`を累積保持する純粋PowerShell関数を追加した。
- 旧更新でfalseへ上書きされた実機stateは、正規rollback metadata内の同一thumbprint履歴から3フラグをtrueへmigrationした。
- upgrade時に、現行thumbprintではなく、subject/issuerがともに`CN=Skill Magnet Local`、code-signing EKUを持ち、CurrentUser/LocalMachineのTrustedPeople双方に存在する証明書だけを旧製品所有trustとして削除する。subjectだけが同じ証明書や現行証明書は削除しない。
- install transactionが削除した旧thumbprint一覧を、後続status readbackで捨てずCLI結果へ保持する。
- `ContextMenu.rollback.interrupted-*` / `recovered-*`は、正規path・厳密なtimestamp名・非link・backup.json versionを満たす製品所有物だけ削除する。実機の旧3件は削除済み。
- UAC取消時はAppx削除後も証明書state、external root、rollback pointを保持し、完了扱いせず再試行可能に停止することを実機確認した。

### Desktop入力固定

- 確認済みINDEXと全skill directoryをcontract専用`desktop-materializations/<contract_id>`へコピーし、コピー後hashを確認時hashと照合する。
- Desktop promptは元repoではなくmaterializationの絶対pathとdigestだけを参照する。元SKILL.mdをhandoff後に変更してもmaterialized bytesとpromptが変化しないテストを追加した。
- materializationにはcontract、commit、expiry、INDEX/skill hashesを記録し、期限切れと中断tempを次回public entryで回収する。

### CI・監査・文書

- GitHub Actions checkoutを`submodules: recursive`へ変更し、WindowsでPowerShell ownership testとnative buildを追加した。
- Windows CIに、非対話の管理者runnerだけで有効にする証明書trust経路と、実MSIX install/status/rollback/uninstall・全残留ゼロ検査を追加した。通常利用時のUAC経路は維持する。
- wheel build→隔離`pip --target` install→bundled config/pack/native/menu command検証をWindows/macOS共通suiteへ追加した。
- macOS CIに、実Quick Actionを`~/Library/Services`へinstallし、`/usr/bin/automator`で選択folderを渡し、adapter到達後にuninstallしてworkflow/transaction残留ゼロを確認するrelease lifecycleを追加した。
- `integration/explorer_results_gate.py`とテストを復旧し、現在のtest count、1 package leaf、selection kind、9 skillsを実configへ照合する。
- Explorer結果正本とREADMEを現行package選択仕様へ更新し、旧個別leaf計画はアーカイブ表示にした。
- `.e2e-state`、native output、compiler/linker生成物、証明書/package生成物をignoreへ追加した。

## 検証結果

| 検証 | 結果 |
|---|---|
| `python -m unittest discover -s tests -v` | PASS。117 tests / 133.453s、条件付き1 skip。最終remote Windows/macOS suiteも全件green |
| wheel隔離install | PASS。既定config、固定commit、9 skills、native scripts、1 package leafを確認 |
| PowerShell certificate ownership test | PASS |
| Windows native build | PASS。DLL/launcher生成・署名成功 |
| 実wheel install | PASS。Appx `0.3.0.0`、usable、Directory/Background、1 package leaf |
| 実署名readback | DLL / launcher / MSIXすべて`Valid`、同一thumbprint |
| ownership migration | PASS。3フラグtrue、旧transaction residue 3件削除 |
| uninstall UAC取消 | EXPECTED FAIL-CLOSED。Appx削除後、cleanup未完了を成功扱いせずstate保持 |
| `pip check` | PASS |
| `git diff --check` | PASS（改行変換warningのみ） |
| results gate | PASS |
| remote macOS CI | PASS。117 tests、実Quick Action install/Automator実行/selected path probe/uninstall/残留ゼロ、run `33246388633` |
| remote Windows CI | PASS。117 tests、ownership、旧証明書2世代cleanup、native build、実MSIX lifecycle、残留ゼロ、run `33246388633` |
| Windows実機旧trust cleanup | PASS。旧7 unique thumbprintsがCurrentUser/LocalMachine TrustedPeopleとも0。現行`022B95...`のみ残存 |
| Windows実機最終status | PASS。0.3.0.0、1 package leaf、config一致、`usable_installed_state: true` |
| Windows Explorer実UI | PASS。BEADS folder右クリック→`Skill Magnet`→`PMO`→確認UI→Codex→最終確認 |
| Desktop handoff | PASS。contract `d372a02620e84f01a9a6e326d1826ba7`、selection kind `pack`、9 skills、immutable materialization、`desktop_handoff_ready` |

## 残存リリースゲート

1. Codex Desktop新規taskに送信されたpromptと自然文回答を確認し、contract ID・prompt hash・回答を人手受入記録へ束縛する。

Windows Explorer実入口、Desktop handoff、Windows実MSIX lifecycle、macOS実Automator lifecycle、両OSの残留ゼロは完了した。残る1件は製品がDesktop task結果を取得できない外部UIゲートであり、自動試験の成功として代替しない。

公開配布境界: 現在のMSIXは製品仕様どおりローカル自己署名であり、Microsoft Store等へ公開する正式publisher identityではない。公開配布には外部の証明書・配布アカウント・秘密鍵管理が必要であり、これらを受領していない状態をGOへ読み替えない。

CI証拠: 実装HEAD `76aad582945b4b692bfb24b3fe725bf871de2ad7`の[GitHub Actions run 33246388633](https://github.com/omusubiman5/skill-magnet/actions/runs/33246388633)
