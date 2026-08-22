# スキル保管庫契約

Skill Magnet本体とは別のユーザー所有GitHub repositoryが、唯一のスキル正本です。Skill Magnetはconfigでrepository URLと完全な40文字commit SHAを固定し、cloneのorigin、HEAD、clean working tree、owner allowlistを照合します。

各packには、config上で次が必要です。

```json
{
  "id": "my-pack",
  "repo_url": "https://github.com/my-owner/my-skills.git",
  "expected_commit": "0123456789abcdef0123456789abcdef01234567",
  "source": "/verified/local/clone",
  "skills": ["my-skill"],
  "approved_by": "user-identity",
  "approved_at": "2026-08-22T00:00:00+09:00",
  "purpose": "When and why this pack should be selected."
}
```

各skill directoryには `SKILL.md` と `acceptance.json` が必要です。

```text
my-skill/
├── SKILL.md
└── acceptance.json
```

MVPの `acceptance.json` は、Codexの構造化結果 `result` に対する完全一致条件を一つ以上定義します。

```json
{
  "version": 1,
  "assertions": [
    {
      "path": "result.decision",
      "equals": "bounded"
    }
  ]
}
```

現在のMVPで許可するpathは `result.<field>` の一階層だけです。複数skillのassertionが同じfieldへ異なる値を要求する場合はfail-closedです。acceptanceがない、空、壊れている、判定不能、またはCodex結果と一致しない場合、`verified_applied` は発行されません。

`acceptance.json` は単なる自己申告ではなく、skillの目的に固有で機械判定可能な結果を要求してください。ただし、LLMの内部状態を公式に保証するものではありません。Skill Magnetは、選択commitのinstruction digest、起動ごとのchallenge nonce、Codexの構造化出力、skill固有assertionを組み合わせ、証拠が不足する時に誤って成功と表示しないための仕組みです。
