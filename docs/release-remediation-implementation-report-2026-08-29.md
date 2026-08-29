# Skill Magnet 0.3.0 release remediation implementation report

> **Superseded / NO-GO:** この文書の0.3.0 GO判定はSmart App Control error 4551の実機反証により撤回しました。現行報告は[`smart-app-control-remediation-report-2026-08-29.md`](smart-app-control-remediation-report-2026-08-29.md)です。

日付: 2026-08-29  
対象: `C:\Projects\skill-magnet`  
計画: [`release-remediation-implementation-plan-2026-08-29.md`](release-remediation-implementation-plan-2026-08-29.md)

## 結論

NO-GOレビューでコード修正対象となった、壊れたwheel、固定project path、証明書ownership消失、旧自己署名trust残留、rollback異常残留、Desktop入力TOCTOU、削除された結果gate、旧個別leaf文書、版不整合、CIのsubmodule/native欠落を修正した。0.3.0 wheelをrepository外の`site-packages`へ導入し、そのwheel内native sourceからのbuild、MSIX署名、実Appxの初回install・update・直前版rollback・uninstallまで成功した。現在の実機はcanonical wheelから再登録した`SkillMagnet.ContextMenu_0.3.0.0_x64`のusable状態である。

Windows/macOS CIは、120テスト、canonical wheel payload、wheel-installed native build、証明書ownership、実OS integration lifecycleをgreenで完走した。Windows 0.3.0実機ではExplorerから単一`PMO`を選んでCodex Desktopへhandoffし、ユーザーが新規taskの自然文回答まで確認した。回答はINDEXに従ってPMOパックの必要な境界・レビュー能力を組み合わせ、BEADS文書2件へ具体的なrelease blockerを返し、対象外能力を適用しなかったことも説明した。したがって、このrepositoryが定義するローカル自己署名版0.3.0候補は`GO`とする。handoff証拠自体を回答完了へ読み替えず、自然文結果はユーザー実機受入として別記録した。Microsoft Store等の公開配布だけは、正式publisher identityが未提供のため別途`NO-GO`である。

## 実装内容

### 配布artifact

- `setup.py`のbuild hookでWindows native source、既定config、固定commit `c7747bba…`の9-skill packをwheelへ収録した。
- 固定packはcheckoutの改行変換済みbytesをコピーせず、固定commitのGit blobを直接収録する。Windows/macOSで同じ論理payload hashになり、`codex-sandbox-approval-boundary/SKILL.md`は正本SHA-256 `e7a9def6…`と一致する。
- bundled snapshotはrepo URL、commit、INDEX digest、各skill directory digestを実行時検証する。Git metadataがないwheel内でも改変をfail-closedにする。
- `package://codex-pmo-skills-c7747bba`を導入し、source checkoutではsubmodule、wheelではbundled snapshotへ解決する。
- wheelの既定configとinstalled `site-packages`を使うため、生成されたExplorer commandから`C:\Projects\skill-magnet`固定参照を除去した。
- Python版を`0.3.0`、MSIX版を`0.3.0.0`へ同期した。

### 証明書・rollback・uninstall

- 同一thumbprintの更新では`created_my`、`created_trusted_people`、`created_machine_trusted_people`を累積保持する純粋PowerShell関数を追加した。
- 旧更新でfalseへ上書きされた実機stateは、正規rollback metadata内の同一thumbprint履歴から3フラグをtrueへmigrationした。
- cleanup対象はstateが明示的に保持する`owned_certificate_thumbprints`だけに限定した。subject/issuer/EKU/store一致は所有権の代替に使わず、同じ属性のlookalike証明書を削除しない。
- install transactionが削除した旧thumbprint一覧を、後続status readbackで捨てずCLI結果へ保持する。
- `ContextMenu.rollback.interrupted-*` / `recovered-*`は、正規path・厳密なtimestamp名・非link・backup.json versionを満たす製品所有物だけ削除する。実機の旧3件は削除済み。
- update成功時は直前の導入状態をrollback pointへ入れ替え、rollbackはその直前版を復元する。uninstallは現在版、rollback point、製品所有証明書、external root、classic/modern登録を削除する。
- UAC取消時はAppx削除後も証明書state、external root、rollback pointを保持し、完了扱いせず再試行可能に停止することを実機確認した。

### Desktop入力固定

- 確認済みINDEXと全skill directoryをcontract専用`desktop-materializations/<contract_id>`へコピーし、コピー後hashを確認時hashと照合する。
- contractにINDEX digestと全skill hashを含め、検証後からcopy直前にINDEXだけを差し替える競合も`INDEX changed`でfail-closedにする。
- Desktop promptは元repoではなくmaterializationの絶対pathとdigestだけを参照する。元SKILL.mdをhandoff後に変更してもmaterialized bytesとpromptが変化しないテストを追加した。
- materializationにはcontract、commit、expiry、INDEX/skill hashesを記録し、期限切れと中断tempを次回public entryで回収する。
- cleanupはactivation専用commandだけでなく、`packs`や`status`を含む全public CLI entryで先に実行する。

### CI・監査・文書

- GitHub Actions checkoutを`submodules: recursive`かつ全履歴取得へ変更し、台帳のrelease code SHAが実在し、その後artifact入力が変わっていないことを検査する。
- `actions/checkout@v5`、`actions/setup-python@v6`、`actions/upload-artifact@v7`を使い、両OSのwheel候補をCI artifactとして保存する。
- Windows CIに、非対話の管理者runnerだけで有効にする証明書trust経路と、実MSIX install/status/rollback/uninstall・全残留ゼロ検査を追加した。通常利用時のUAC経路は維持する。
- wheel build→強制install後、import元がcheckout外であることを確認し、そのwheel内config/pack/nativeだけで後続release lifecycleを実行する。
- macOS CIに、実Quick Actionを`~/Library/Services`へinstallし、`/usr/bin/automator`で選択folderを渡し、通常の製品`context` adapterへ到達した証拠を排他的に作成してからuninstallし、workflow/transaction残留ゼロを確認するrelease lifecycleを追加した。
- `integration/explorer_results_gate.py`とテストを復旧し、現在のtest count、1 package leaf、selection kind、9 skillsを実configへ照合する。
- Explorer結果正本とREADMEを現行package選択仕様へ更新し、旧個別leaf計画はアーカイブ表示にした。
- `.e2e-state`、native output、compiler/linker生成物、証明書/package生成物をignoreへ追加した。

## 検証結果

| 検証 | 結果 |
|---|---|
| `python -m unittest discover -s tests -v` | PASS。120 tests / 144.707s、条件付き1 skip。remote Windows/macOS suiteも全件green |
| canonical wheel | PASS。2回buildの論理payload SHA-256がともに`72b664b4…`。Windows/macOS CIでも同値 |
| wheel単独install | PASS。import元はrepository外`site-packages`。既定config、固定commit、9 skills、native scripts、1 package leafを確認 |
| PowerShell certificate ownership test | PASS |
| Windows native build | PASS。DLL/launcher生成・署名成功 |
| 実wheel install | PASS。Appx `0.3.0.0`、usable、Directory/Background、1 package leaf |
| 実署名readback | DLL / launcher / MSIXすべて`Valid`、同一thumbprint |
| ownership migration | PASS。3フラグtrue、旧transaction residue 3件削除 |
| uninstall UAC取消 | EXPECTED FAIL-CLOSED。Appx削除後、cleanup未完了を成功扱いせずstate保持 |
| `pip check` | PASS |
| `git diff --check` | PASS（改行変換warningのみ） |
| results gate | PASS |
| remote macOS CI | PASS。120 tests、canonical wheel、実Quick Action install/Automator→製品adapter/uninstall/残留ゼロ、run `33248318073` |
| remote Windows CI | PASS。120 tests、canonical wheel、ownership、wheel-installed native build、実MSIX install→update→rollback→uninstall、残留ゼロ、run `33248318073` |
| Windows実機旧trust cleanup | PASS。旧7 unique thumbprintsがCurrentUser/LocalMachine TrustedPeopleとも0。現行`022B95...`のみ残存 |
| Windows実機最終status | PASS。0.3.0.0、1 package leaf、config一致、`usable_installed_state: true` |
| Windows Explorer実UI | PASS。BEADS folder右クリック→`Skill Magnet`→`PMO`→確認UI→Codex→最終確認 |
| Desktop handoff | PASS。contract `d372a02620e84f01a9a6e326d1826ba7`、selection kind `pack`、9 skills、immutable materialization、`desktop_handoff_ready` |
| Desktop自然文結果 | PASS（ユーザー実機確認）。INDEXルーティング、必要能力の組合せ、具体的文書レビュー、対象外能力の不適用、ファイル変更なしを確認 |

## 残存リリースゲート

ローカル自己署名版0.3.0について、コード・配布物・CI・Windows実機・Desktop自然文結果の残件はない。Desktop回答は製品が機械取得した証拠ではなく、ユーザーが確認した実機受入であるという境界は維持する。

公開配布境界: 現在のMSIXは製品仕様どおりローカル自己署名であり、Microsoft Store等へ公開する正式publisher identityではない。公開配布には外部の証明書・配布アカウント・秘密鍵管理が必要であり、これらを受領していない状態をGOへ読み替えない。

Release code SHA: `b4f68209a2c898879c3f279ce7080ca7301a186b`。CI証拠: [GitHub Actions run 33248318073](https://github.com/omusubiman5/skill-magnet/actions/runs/33248318073)
