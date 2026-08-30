# Skill Magnet 製品完成 NO-GO 是正報告

日付: 2026-08-30  
計画: [`product-completion-remediation-plan-2026-08-30.md`](product-completion-remediation-plan-2026-08-30.md)

## 現在の判定

**NO-GO / 実装継続中**

Smart App Control 4551修正、full MSIX、wheel単独性、terminal cleanup hardeningを含む139テストはローカルでPASSしている。0.3.5 candidateはWindows/macOS CI run `33307525539`でPASSしたが、0.3.6 candidateのCIは未実行である。これは製品完成ゲートの一部にすぎず、実Desktop自然文結果などの未達を埋めない。

## 再監査で確認した未達

- 現行`Delivery Assurance / 8f12af5…`へ更新後のCodex Desktop自然文結果がない。
- 再試験ではOSへのhandoffまで成功したが、distinctな新規taskを観測できず、結果は`verified_completed=false`である。
- Finder CIはpack検証、contract、materialization、delivery境界まで通るが、release probe専用分岐で通常のpack/runtime選択、確認UI、実AI起動を通らない。
- 自動E2EはSkill Magnet本体＋独立pack→handoff→completion receipt→INDEX適用部分集合→非空成果→acceptanceまで通るようになったが、runtime成果はfixtureであり、実Desktop taskの証拠ではない。
- 現行packで複数skillをINDEXに従って組み合わせた実行証拠がない。
- Desktop handoff単独は正しく`verified_completed=false`であり、現行実機試験はcompletion receipt導入前なので三段階成功条件も未達である。
- Web Claudeは製品経路として統一済みだが、実conversationへのprompt反映、全skill読了、INDEX適用、自然文結果の証拠がない。
- request-aware acceptanceはJSON型と自己申告ruleを検査するだけで、依頼に対する意味的妥当性を独立検証できない。
- Finderの通常UIフロー、公開署名・notarization、配布元・更新経路が未受入である。

## 今回実施した検証

- `python -m unittest discover -s tests -v`: 139件PASS、環境依存1件skip。
- 0.3.6 candidate artifact-input commit: `e52c04e3d8e1b8a5ef842cc77e5745d31d74c4e9`。
- 0.3.6 canonical wheel logical payload SHA-256: `82238c81583a38149f1877f46b43154ee73033e2774d66b81af35526966a451e`。独立したローカル2buildで一致した。CI照合は未実行。
- 0.3.5 CI run [`33307525539`](https://github.com/omusubiman5/skill-magnet/actions/runs/33307525539): Windows/macOSともgreen。両OS138 test、wheel payload gate、macOS Finder lifecycle、Windows certificate/native/MSIX lifecycleがPASSした。
- 0.3.4 CI run [`33307128048`](https://github.com/omusubiman5/skill-magnet/actions/runs/33307128048): Windows/macOSともgreen。両OS136 test、wheel payload gate、macOS Finder lifecycle、Windows certificate/native/MSIX lifecycleがPASSした。
- 0.3.3 canonical wheel logical payload SHA-256: `1c1ab2914220d4f468614efd9f8a70a845d336c91864f75480c47a584cd2feb1`。独立したローカル2buildで一致し、CIのstandalone wheel payload gateもPASSした。
- completion receipt追加後のCI run [`33306460511`](https://github.com/omusubiman5/skill-magnet/actions/runs/33306460511): Windows/macOSともgreen。両OSで132 test、standalone wheel install、candidate SHAとwheel payload gateがPASSした。Windowsは証明書state、native build、MSIX install/update/rollback/uninstall lifecycle、macOSはFinder semantic lifecycleもPASSした。
- 現行policy、MVP設計、Explorer正本台帳、Finder lifecycle、Claude adapter、INDEX parserを再読した。
- 独立鬼再レビュー: P0 3件、P1 4件、P2 1件、P3 0件、NO-GO。

## 実装済みの是正

- Finder release lifecycleを、選択pathを書いて終了するprobeから、独立pack検証、launch contract、INDEX/全skill materialization、Codex Desktop delivery境界まで通すsemantic E2Eへ変更した。
- Finder semantic E2EはCodexとClaudeの両runtimeを別々に実行し、Codexは`codex://threads/new`、Claudeは`https://claude.ai/new`へ一つのpromptを渡すこと、handoffをcompletionへ昇格しないことを検査する。
- Windows/macOSのClaude製品経路をWeb Claude新規conversation prefillへ統一した。clipboard、既存conversation、常設plugin、headless `claude --print`へのproduct fallbackは廃止した。headless adapterは回帰試験用だけに限定した。
- INDEX parserとverificationへ`composes-with`を追加した。両端skillを同時適用する場合、両ID、`composes-with`、依頼固有の組合せ理由をapplied ruleへ要求する。
- package acceptanceの固定equalsを、同じJSON型の依頼固有値＋該当`result.*`を示すapplied ruleへ変更した。個別skill選択では従来どおり固定equalsを維持する。
- 実configと独立Delivery Assurance packを使い、Windows/macOS×Codex/Claudeのcontract、materialization、handoff境界を通す自動E2Eを追加した。
- README、MVP設計、Windows modern menu設計、Smart App Control報告書のscope矛盾を訂正した。
- `build/lib`を再利用したwheelへ廃止済みpackが残る欠陥を修正した。package出力を毎回初期化し、意図的に古いpackを置いてもwheelへ混入しない回帰試験を追加した。
- Codex Desktop task用の期限付きcompletion receiptを追加した。handoff時にschemaと一回限りreceiptを作り、task完了後にcontract、challenge nonce、request hash、pack provenance、INDEX関係、適用部分集合、acceptanceを検証して初めて`verified_completed`へ遷移する。改ざん、再利用、期限切れ、OS起動失敗ではreceipt、schema、output、materializationを回収し、成功証拠を作らない。
- 鬼再監査で実証された`prompt_sha256`改ざん、並行二重callback、期限切れ直撃、malformed receipt残留を0.3.4で修正した。prompt/schema digestは消費済みcontract recordのhandoff bindingとも照合し、callbackは原子的lockで一つだけclaimする。全verification failureをnegative terminalへ確定し、安全に導出したreceipt/schema/output/materializationだけを即時回収する回帰試験を追加した。
- 0.3.4再監査で実証されたbinding+receipt同時改ざんと、cleanup後・success terminal前の競合を0.3.5で修正した。desktop bindingをcontract digest本体へ含め、success/rejectionの排他的terminal証拠がatomic writeされるまでclaim lockを保持する。malformed/expired recoveryもnegative terminalを残す。
- 0.3.5再監査で実証されたsuccess terminal書込み後のlock cleanup失敗を0.3.6で修正した。verified terminalがatomic write済みならlock削除失敗をretriable GCとして扱い、矛盾するnegative terminalを生成しない回帰試験を追加した。

## CI再現性障害と修正

run `33295357619`ではWindows/macOSの127 testがPASSした一方、wheel gateが失敗した。ローカルwheelだけに旧`codex-pmo-skills-c7747bba`が残っており、CIのclean buildには存在しなかった。原因はcustom `build_py`が既存`build/lib/skill_magnet`を消さずに再利用したことだった。

0.3.2の再現性修正ではpackage rootを先に削除してから現在のsource、固定pack、native assetsだけを構築した。当時の独立した2回のローカルbuildは、当時のCIと同じlogical payload SHA-256 `5f6972b6…a625`になった。これは現行0.3.5のhashではない。

履歴candidate 0.3.3のCI run [`33306460511`](https://github.com/omusubiman5/skill-magnet/actions/runs/33306460511)と0.3.4のrun [`33307128048`](https://github.com/omusubiman5/skill-magnet/actions/runs/33307128048)はWindows/macOSともgreen。ただし実Desktop自然文結果と人手UI受入はCIの範囲外であり、製品GOには昇格しない。

## 現行pack Desktop実機再試験

2026-08-30T05:39:30Z、現行`Delivery Assurance / 8f12af5…`、全9skill、複数skillを必要とするread-only依頼で`codex://threads/new`へhandoffした。contractは`612ac22908dd4d1aa70225728c631b71`、prompt SHA-256は`d0a3140b2fe0498d4e06303496f6bc6dc24baf594cc360e97aaa83af930594a9`。

handoff後、Codex task一覧に新規taskが現れず、自然文結果は取得できなかった。したがって結果は`desktop_handoff_ready`、`desktop_result_verification=not_available`、`verified_completed=false`のままであり、成功証拠へ数えない。証拠は[`test-evidence/delivery-assurance-desktop-20260830/handoff-log.json`](test-evidence/delivery-assurance-desktop-20260830/handoff-log.json)に保存した。

## 次の更新条件

この報告書は各Phaseの修正、追加test、実機証拠、CI URL、artifact hash、残留検査を実施するたびに更新する。現時点では現行packの実Desktop自然文結果、実Finder UI、公開署名・notarizationが未達である。全完成ゲートがPASSし、独立鬼レビューがP0〜P3すべて0になるまでGOへ変更しない。
