# trans-instrumental

イメージボード用に、オーケストラの各楽器奏者の3Dモデルを作るためのパイプライン。
生成したモデルは Max/Jitter に読み込み、回転させながら「溶ける」「散る」「別の人になる」といったエフェクトをかける。

参考にしているトーン：モノクロで高コントラスト。白いプリーツの長衣、または黒い衣装。
ミラー反転、多重露光、落下する身体、極端なローアングル。

## 構成

| パス | 内容 |
| --- | --- |
| `models/manifest.json` | 奏者29人（オーケストラ全楽器と指揮者）の定義。楽器、姿勢、衣装、プロンプト |
| `scripts/meshy_generate.py` | Meshy の text-to-3D API で GLB を生成する。結果は `models/raw/<id>/` に保存 |
| `scripts/glb_to_pointcloud.py` | GLB を同じ点数・同じ並び順の点群（`.jxf`）に変換する。保存先は `models/points/` |
| `max/performer_fx.genexpr` | jit.gen 用のコード。点群を変形（モーフ）、融解、飛散させる |

## 1. モデル生成（Meshy）

必要なものは2つ。

- APIキー：次のどちらか
  - クラウド環境の「Network secrets」に登録する（推奨）。設定は下記
  - 環境変数 `MESHY_API_KEY` に入れる（ローカルで実行するとき）

クラウド環境の「Network secrets」の設定：

| 項目 | 入れる値 |
| --- | --- |
| Secret type | `Bearer` |
| Name | `Meshy` など、わかりやすい名前 |
| Allowed websites | `api.meshy.ai` |
| Custom headers | Name `Authorization`、Prefix `Bearer`、Value にMeshyのAPIキー |

Network secrets に登録したホストには、ネットワークのアクセスレベルに関係なく接続できる。

```sh
python3 scripts/meshy_generate.py --dry-run          # プロンプトの確認だけ（課金なし）
python3 scripts/meshy_generate.py --tier 1           # 各セクション代表の6人で試す
python3 scripts/meshy_generate.py                    # 全員を生成
python3 scripts/meshy_generate.py --ids oboe horn    # 指定した奏者だけ生成
python3 scripts/meshy_generate.py --no-refine        # 形状のみ（テクスチャなし、安い）
```

- 生成済みの奏者はスキップする。中断しても `task.json` から再開できる。
- tier 1 は各セクションの代表で、violin / cello / harp / flute / trumpet / timpani の6人。全員を回す前にトーンを確認する用。
- 衣装や雰囲気を変えたいときは、`manifest.json` の `style.suffix` と `wardrobe` を編集する。

## 2. Max 用の点群に変換

```sh
pip install trimesh numpy pillow
python3 scripts/glb_to_pointcloud.py                 # models/raw/*/*.glb をすべて変換
python3 scripts/glb_to_pointcloud.py --size 512 512  # 点数を増やす（262,144点）
```

出力は1人あたり次の3つ。

- `<id>_pos.jxf`：位置。float32、3プレーン、256×256
- `<id>_col.jxf`：グレースケールの色。float32、4プレーン
- `<id>.npz`：同じデータを Python で扱うためのもの

点群の性質：

- どの奏者も「足元 y=-1、頭 y=+1」に正規化してある。
- 点は「高さの帯 → 体の周りの角度」の順に並べてある。そのため、別の奏者でも同じインデックスの点はおおよそ同じ部位に来る。
- 頂点数がそろっているので、メッシュのトポロジーが違っていてもそのまま補間できる。「別の人になる」はこの性質を使っている。

## 3. Max/Jitter での使い方（例）

```
[jit.matrix posA 3 float32 256 256]  ← read violin_pos.jxf
[jit.matrix posB 3 float32 256 256]  ← read cello_pos.jxf
        ↓ in1          ↓ in2
[jit.gen @file performer_fx.genexpr]   param: morph / melt / scatter / spread / seed
        ↓
[jit.gl.mesh ctx @draw_mode points @point_size 2]   ← 色は <id>_col.jxf を色用インレットへ
回転：jit.gl.mesh の @rotatexyz を line や jit.anim.drive で動かす
```

- `morph` 0→1：A の人から B の人に変わる。点ごとにタイミングをずらしてある。
- `melt` 0→1：頭から崩れて床に流れ落ち、広がる。
- `scatter` 0→1：体の中心から外へ飛び散る。

既存の Max パッチのエフェクトは、`jit.gen` の出力（位置マトリクス）か、`jit.gl.mesh` の手前に差し込めばそのまま使える。

> 注意：`.jxf` の書き出しと `performer_fx.genexpr` は、まだ Max 上で確認していない。
> 読み込めない場合は `.npz` から別の形式に書き出し直す。

## 生成データの扱い

`models/raw/`（GLB）と `models/points/` は容量が大きいので `.gitignore` に入れてある。
リポジトリで共有したい場合は Git LFS を使う。
