# Skill Magnet test証拠監査と最新実機error診断

日付: 2026-08-28
対象: `C:\Projects\skill-magnet`
契約: `ORD-20260828-12`
判定: **自動test PASSは実Codex/Explorer/Tk E2E成功を証明していない。最新実機attemptは`runtime_failed`であり、具体的なruntime終了理由は現行保存証拠から復元不能。**

## 1. 最新実機errorの直接証拠

スクリーンショット `C:\Users\HOMEA\AppData\Local\Temp\codex-clipboard-829da158-3f39-4f43-a20b-e68ff522d584.png` は、2026-08-28 21:18:30 JSTに保存され、次を表示している。

```text
実行に失敗しました
原因
選択したAIのverification processが完了前に終了しました。
```

最新stateとnative invoke logは同じattemptを次のように一意に示す。actual request本文は記載せず、UTF-8 SHA-256だけを示す。

| 項目 | 実値 / 証拠 |
|---|---|
| native Invoke | `2026-08-28T12:16:06.245Z`、`invoke_enter` |
| selection | `2026-08-28T12:16:06.249Z`、`selection_succeeded` |
| CreateProcess | `2026-08-28T12:16:07.009Z`、success、PID `13488` |
| contract ID | `eecaee60593642ce889b8a35caf180d2` |
| attempt ID | `79dac29d52d34ae69457a21308c15954` |
| confirmed / consumed | `2026-08-28T12:16:57.620454Z` / `2026-08-28T12:16:57.815866Z` |
| selected skill | `codex-sandbox-approval-boundary` |
| runtime | `codex` |
| actual-request SHA-256 | `bb6954a3390b14c0f3a69d47a9de8c09061dd10f19130645766c50f7aa2c0e86` |
| lifecycle / negative evidence | `runtime_failed`、terminal event `3711c48425b04f86b2a5980af19a6099` |
| negative evidence保存時刻 | `2026-08-28T21:17:10.0614115+09:00` |
| current CLI | `codex-cli 0.148.0` |

証拠path:

- `C:\Users\HOMEA\AppData\Local\SkillMagnet\ContextMenu\invoke.log`
- `C:\Users\HOMEA\.skill-magnet\launch-contracts\eecaee60593642ce889b8a35caf180d2.json`
- `C:\Users\HOMEA\.skill-magnet\events\eecaee60593642ce889b8a35caf180d2-lifecycle.jsonl`
- `C:\Users\HOMEA\.skill-magnet\evidence\eecaee60593642ce889b8a35caf180d2-not-guaranteed.json`

### 確定できる直接原因

`src/skill_magnet/activation.py:879-887` はruntimeを`subprocess.run(..., capture_output=True)`で同期実行する。`activation.py:893-896` はreturn code非0を `_RuntimeFailed` に変換する。`src/skill_magnet/ui.py:197-203` がその型をスクリーンショットの文言へ写像する。

従って表示の直接原因は、**実Codex verification childが非0 return codeで終了したこと**である。COM/selection/CreateProcessはnative log上successであり、今回の最初のfailureではない。

### 確定できない下位原因と証拠欠落

現実装は次を保存しない。

- `activation.py:893-896` は `_RuntimeFailed` にreturn codeを持たせない。
- `activation.py:892` が一時eventへ書くのは`stdout`だけで、`stderr`は書かない。
- `activation.py:944-948` は失敗時にschema/output/event/process markerをcleanupする。
- `activation.py:966-980` のnegative evidenceにはstatus/reasonはあるが、runtime exit code、stderr digest、redacted diagnosticがない。

最新negative evidenceにも`runtime_exit_code`、`runtime_stderr`、`runtime_stdout`は存在しない。このため、config parse、認証、network、CLI option、model/API、その他runtime errorのどれで非0終了したかは、現在の保存物だけでは確定不能である。過去attemptで確認された`invalid transport`を今回の原因と再認定する証拠もない。

## 2. ORD-09/10 test commandと結果の監査

### 既存記録に完全なcommandとmethod名が残るもの

`docs/fix-report-result-surface-2026-08-28.md` に次のfocused commandが全文保存されている。

```text
python -m unittest \
  tests.test_activation.ActivationEndToEndTest.test_codex_verification_uses_process_local_mcp_overrides \
  tests.test_activation.ActivationEndToEndTest.test_verification_session_is_not_resumed_and_surface_hides_raw_json \
  tests.test_activation.ActivationEndToEndTest.test_failed_and_blocked_surfaces_are_japanese_and_never_success \
  tests.test_activation.ActivationEndToEndTest.test_windows_context_collects_actual_request_before_execution \
  tests.test_activation.ActivationEndToEndTest.test_windows_context_failure_returns_without_console_output \
  tests.test_activation.ActivationEndToEndTest.test_completion_contract_rejects_each_mismatched_claim
```

結果記録:

```text
Ran 6 tests in 4.285s
OK
```

同記録のfull/compile/diff:

```text
python -m unittest discover -s tests
Ran 103 tests in 97.291s
OK (skipped=1)

python -m compileall -q src tests
exit 0

git diff --check
exit 0
```

### ORD-10最終報告で結果行だけが残り、正確なmethod listが欠落しているもの

`docs/fix-report-completion-cli-window-2026-08-28.md` は次だけを記録している。

```text
Ran 8 tests in 8.592s
OK

Ran 4 tests in 7.598s
OK

SkillMagnet IExplorerCommand contract PASS (Python host)

Ran 104 tests in 101.184s
OK (skipped=1)
```

full command、compile、diffは次のとおり記録されている。

```text
python -m unittest discover -s tests
python -m compileall -q src tests
git diff --check
```

しかし、8件/4件のunittest command lineと完全なmethod名は同報告にもrepository内の他記録にもない。source上の説明から候補methodを推測することは可能だが、実行した正確なcommandとして報告することはできない。これはtest実体監査の証拠欠落である。

説明と現行test codeが対応する候補は次だが、**実行commandとしては未証明**である。

- `test_codex_verification_uses_process_local_mcp_overrides`
- `test_same_actual_request_fixture_reaches_verified_completed`
- `test_verification_session_is_not_resumed_and_surface_hides_raw_json`
- `test_failed_and_blocked_surfaces_are_japanese_and_never_success`
- `test_runtime_failures_show_one_error_and_never_verify`
- `test_explorer_menu_cancel_before_leaf_has_zero_side_effects`
- `test_windows_leaf_command_builder_preserves_independent_argv`
- `test_windows_menu_command_bootstraps_outside_project_directory`

native Python contractのsourceは `native/windows-modern-context-menu/contract_test.py` であり、成功行は上記のとおり保存されている。報告は独立contractの完全な起動commandとDLL pathを保存していない。一般的な呼出し形は次だが、ORD-10で実行された文字列そのものとしては未証明である。

```text
python native/windows-modern-context-menu/contract_test.py <SkillMagnetCommand.dll>
```

## 3. testが検証したもの / していないもの

| test / 証拠 | 検証したこと | mock / fake / fixture境界 | 検証していないこと |
|---|---|---|---|
| `test_codex_verification_uses_process_local_mcp_overrides` (`tests/test_activation.py:1864-1922`) | 生成argvから`--ignore-user-config`が消え、3 overrideが`exec`前、Windows creation flagが`CREATE_NO_WINDOW` | runtimeは`self.fake_codex`。`subprocess.run`をspyし、最後はfake Pythonを実行 | 実Codex 0.148.0 parser、実user config、認証、API、実stderr、実exit lifecycle |
| `test_same_actual_request_fixture_reaches_verified_completed` (`1924-1951`) | 同じ文字列のhashとfake outputが`verified_completed`になる | `fake_codex.py`をPythonで実行。Codexではない | 実Codexが同じ依頼を完了すること、user config、process lifecycle |
| `_fake_codex` (`413-431`) | stdin envelopeを読んで所定JSONをoutput pathへ書くfixture | model/config/auth/networkなし。常に成功JSONを自己生成 | Codex CLI互換性とfailure条件全般 |
| `test_verification_session_is_not_resumed...` (`1953-2025`) | fake成功後のsurface、resumeしないこと、raw field非表示 | fake Codex + `subprocess.run` spy | 実session、実Codex、Tk window描画 |
| `test_failed_and_blocked_surfaces...` (`2027-2052`) | exception型から日本語dictionaryへのpure mapping | processを一切起動しない | 実failureの原因採取、dialog/Tk表示、stderr |
| `test_runtime_failures_show_one_error...` (`1698-1782`) | synthetic exit 2を`runtime_failed`へ分類しverifiedを作らない | 一時Python script、missing executable、cleanup monkeypatch | 実Codex exit code/stderr、実user config、実Explorer/Tk |
| `test_windows_context_collects_actual_request...` (`2510-2546`) | CLI routingがselection→execute→result surfaceを1回呼ぶ | `ActivationEngine`、Tk selection、result windowを全mock | 実Explorer、実Tk、実Codex、実process |
| `test_windows_context_failure_returns_without_console_output` (`165-215`) | CLI stdout/stderrが空でerror UI callbackが1回 | CLI/error UI mock。console window handleを観測しない | Explorer launcher/conhost/Tk、実Codex |
| `test_windows_leaf_command_builder_preserves_independent_argv` (`2291-2312`) | launcher/Python/config/project等のargv構造 | builderの値比較だけ | launcher起動、Explorer、quotingのOS実解釈、child lifecycle |
| `test_windows_menu_command_bootstraps_outside_project_directory` (`3026-3037`) | unrelated cwdからPython bootstrap `--help`が成功 | leafの先頭launcherを除いたPython部分だけを実行 | installed launcher、Explorer、Tk、Codex |
| `native/.../contract_test.py` | 実DLLのCOM root/leaf列挙、列挙時invoke logなし、実launcher childの`GetConsoleWindow()==0` | Explorerではなくctypes host。childは`sys.executable -c` probe | Explorer shell host、selection/Invoke、Tk、Codex/user config/API |
| real Codex 0.148.0旧argv再現 | 旧argv前半が実user config parseで`invalid transport`、exit 1 | model呼出し前のlocal config parseだけ | 修正後のcurrent argvによる実依頼、current runtime lifecycle |
| full suite | 上記unit/fixture/integration testsの集合がPASS | 多数のfake、mock、一時repo、一時registry adapter | 実Explorer→Tk→実Codex 0.148.0→実user config→結果surfaceの一連E2E |

## 4. 自動PASSと実機failureが両立した直接原因

1. 成功系の中心は `tests/test_activation.py:413-431` のfake Codexである。これはCodex CLI/user configを読まず、必要なJSONを自分で生成してreturn code 0で終了する。
2. process-local override testも `tests/test_activation.py:1879-1896` で実Codexではなく同じfakeを使う。従ってargvの形は検査したが、Codex 0.148.0が実user configと合わせて受理し、実processが完走することは検査していない。
3. 同一actual request testも `tests/test_activation.py:1937-1945` でfake Pythonを指定している。「同じ依頼文字列」は同じでも実行主体が異なる。
4. full suiteはこれらをまとめてPASSしただけで、実Explorer/Tk/Codex E2Eではない。
5. 最新実機ではnative/launcherは成功し、その後の実Codexが非0終了した。fixtureが常に0を返すため、この差は自動testで検出できない。
6. さらに製品がexit code/stderrをnegative evidenceへ保存しないため、実機failure後の監査でも下位原因を確定できない。

従って、直接原因は「test frameworkのfailure」ではなく、**real runtime compatibility/lifecycleをfake successで代替したtestを実機E2E相当として読んだ証拠範囲の誤認**である。

## 5. 修正前に必要な最小再現test

製品修正前に、次の順で1件ずつ追加・実行する必要がある。

1. **real Codex config/parser smoke（model/API呼出しなし）**
   installedと同じCodex 0.148.0、current user config、`codex_process_config_args()`を使い、configを実際にparseする副作用なしcommandを起動する。return codeとredacted stderrを保存し、0でなければ後続へ進まない。秘密値は記録せず、server名・error class・stderr digestだけを残す。
2. **real runtime gated integration**
   明示的opt-in環境だけで、installed runtime commandと同じargv/creation flags、隔離project、固定の短いread-only requestを実Codexへ渡す。return code、開始/終了時刻、PID、redacted stderr、terminal lifecycleを同一attemptへ結ぶ。これは通常full suiteへ混ぜない。
3. **failure evidence contract**
   synthetic exit codeとstderrを与え、negative evidenceにexit code、stderr SHA-256、秘密を除いた短いerror classが残ることを固定する。現在のように`runtime_failed`だけを残して原因を消さない。
4. **手動受入gate**
   自動testとは別に、Explorer leaf→実Tk→実Codex→result/failure surfaceをユーザーが1回確認し、同じcontract ID/attempt IDと照合する。これを満たすまで「実機E2E PASS」と呼ばない。

## 6. 監査上の結論

- `full suite PASS`はrepository regression suiteのPASSであり、実機Explorer/Codex E2Eではない。
- 最新errorは`runtime_failed`として確定し、native Invoke/selection/CreateProcessはsuccess。
- 実Codexが非0終了した具体理由は、exit code/stderrを保存しない現実装のため未確認。
- 過去の旧argv`invalid transport`は別attemptの確定事実だが、今回へ流用してはならない。
- ORD-10のfocused 8件/4件は結果行だけが残り、正確なcommand/method listが欠落している。将来はcommand全文、method名、result line、real/fake境界を同じ報告へ保存する必要がある。
