# Skill Magnet 0.3.0 release remediation implementation report

日付: 2026-08-29  
対象: `C:\Projects\skill-magnet`  
計画: [`release-remediation-implementation-plan-2026-08-29.md`](release-remediation-implementation-plan-2026-08-29.md)

## 結論

NO-GOレビューでコード修正対象となった、壊れたwheel、固定project path、証明書ownership消失、rollback残留、Desktop入力TOCTOU、削除された結果gate、旧個別leaf文書、版不整合、CIのsubmodule/native欠落を修正した。0.3.0 wheelからの隔離install、Windows native build、MSIX署名、実Appx update/installまで成功し、現在の実機は`SkillMagnet.ContextMenu_0.3.0.0_x64`のusable状態である。

リリース判定は、候補commitのremote CI結果と、人の操作を必要とする最終UAC uninstall、Codex Desktop実回答、macOS Finder実機が揃うまで`CONDITIONAL NO-GO`とする。自動試験やhandoff受理を実回答完了へ読み替えない。

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
- `ContextMenu.rollback.interrupted-*` / `recovered-*`は、正規path・厳密なtimestamp名・非link・backup.json versionを満たす製品所有物だけ削除する。実機の旧3件は削除済み。
- UAC取消時はAppx削除後も証明書state、external root、rollback pointを保持し、完了扱いせず再試行可能に停止することを実機確認した。

### Desktop入力固定

- 確認済みINDEXと全skill directoryをcontract専用`desktop-materializations/<contract_id>`へコピーし、コピー後hashを確認時hashと照合する。
- Desktop promptは元repoではなくmaterializationの絶対pathとdigestだけを参照する。元SKILL.mdをhandoff後に変更してもmaterialized bytesとpromptが変化しないテストを追加した。
- materializationにはcontract、commit、expiry、INDEX/skill hashesを記録し、期限切れと中断tempを次回public entryで回収する。

### CI・監査・文書

- GitHub Actions checkoutを`submodules: recursive`へ変更し、WindowsでPowerShell ownership testとnative buildを追加した。
- wheel build→隔離`pip --target` install→bundled config/pack/native/menu command検証をWindows/macOS共通suiteへ追加した。
- `integration/explorer_results_gate.py`とテストを復旧し、現在のtest count、1 package leaf、selection kind、9 skillsを実configへ照合する。
- Explorer結果正本とREADMEを現行package選択仕様へ更新し、旧個別leaf計画はアーカイブ表示にした。
- `.e2e-state`、native output、compiler/linker生成物、証明書/package生成物をignoreへ追加した。

## 検証結果

| 検証 | 結果 |
|---|---|
| `python -m unittest discover -s tests -v` | PASS。116 tests / 155.515s、環境に`pythonw.exe`がない条件付き1 skip |
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

## 残存リリースゲート

1. 候補commitを作成し、remoteのWindows/macOS CIがgreenであること。
2. 対話可能な管理者UACでuninstallを完了し、Appx、CurrentUser/My、CurrentUser/TrustedPeople、LocalMachine/TrustedPeople、ContextMenu、rollbackが全て0であること。その後release wheelを再installする。
3. Windows ExplorerのDirectory/Backgroundから0.3.0の単一`PMO`を実選択し、Codex Desktop新規taskの自然文回答まで確認する。
4. macOS hostでFinder Quick Actionのinstall、起動、uninstall、残留ゼロを確認する。

これらは権限・別OS・人のDesktop結果が必要な外部ゲートであり、ローカル自動試験の成功として代替しない。
