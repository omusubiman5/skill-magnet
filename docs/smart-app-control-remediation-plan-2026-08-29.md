# Smart App Control 4551 remediation implementation plan

## 目的

Windows 11 Explorer の実操作で発生した error 4551 を解消し、状態表示だけではなく、Smart App Control 有効実機で `Skill Magnet → PMO → 実行確認` が開くことをリリース条件にする。

## 失敗の定義

0.3.0 は `SkillMagnetLauncher.exe`、初期 0.3.1 は `SkillMagnetCommand.dll` を sparse MSIX の外部 location から実行・loadしていた。どちらもローカル自己署名であり、LocalMachine TrustedPeople への登録だけでは Smart App Control の Enterprise signing level を満たさなかった。package 登録とfile存在だけを確認した `usable_installed_state=true` は誤判定だった。

## 実装手順

1. 自己署名 launcher をcommand argvから除去し、Authenticode-validな公式Pythonを直接起動する。
2. `SkillMagnetCommand.dll`、identity anchor、生成済みmenu manifestをfull MSIX内へ収容する。
3. statusはpackage install locationのcontent、command target署名、旧launcher不在、config一致を検査する。
4. modern install失敗時にblocked classic adapterへ切り替えず、直前状態へrollbackしてerrorで停止する。
5. native buildのobj/lib/expを専用out directoryへ限定する。
6. unit、native contract、wheel、実MSIX lifecycle、Smart App Control実機Explorer、Code Integrity logを検証する。
7. candidate commitと同一SHAのWindows/macOS CIをgreenにしてからGOを再判定する。

## 受入条件

- Windows 11通常右クリックに単一`Skill Magnet`、配下に単一`PMO`が表示される。
- `PMO`選択後に`Skill Magnet — 実行確認`が開き、4551 dialogが出ない。
- 検証時間帯のSkill Magnet関連Code Integrity 3033/3077が0件。
- invoke logに`create_process_succeeded`が記録される。
- 122 tests、native contract、wheel gate、Windows lifecycle、Windows/macOS CIがgreen。
- install/update/rollback/uninstall後に製品所有Appx、registry、証明書、transaction residueが残らない。

## リリース境界

すべての受入条件が揃うまでNO-GO。statusの`usable_installed_state`、package登録、CI単体のいずれも実機Explorer証拠の代替にしない。
