"""Create missing local configuration files without overwriting existing settings."""

import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    for rel in (
        '工具/api配置.json',
        '工具/symphony/symphony配置.json',
        '工具/剪映/剪映配置.json',
    ):
        target = root / rel
        example = target.with_name(target.stem + '.example.json')
        data = example.read_bytes()
        json.loads(data.decode('utf-8-sig'))
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('xb') as stream:
                stream.write(data)
        except FileExistsError:
            print(f'保留已有配置：{rel}')
        else:
            print(f'已创建本地配置：{rel}')
    (root / 'stories').mkdir(exist_ok=True)
    print('初始化完成。请填写自己的接口密钥、账号和软件路径。')


if __name__ == '__main__':
    main()
