import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '短剧 Skill Studio',
  description: '可持续对话、读取规范、保存记忆并连接 CLI 或 API 的短剧创作工作台',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
