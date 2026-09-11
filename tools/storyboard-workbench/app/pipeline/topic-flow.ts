export function topicCandidateNumbers(text: string): number[] {
  const numerals: Record<string, number> = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5 };
  const numbers = [...text.matchAll(/^[\t ]*(?:#{1,4}[\t ]*)?(?:\*\*)?【选题([1-5一二三四五])】/gmu)]
    .map((match) => numerals[match[1]] ?? Number(match[1]));
  return numbers.length === 5 && new Set(numbers).size === 5 ? numbers : [];
}

export function topicMessageText(text: string): string {
  if (text.startsWith('本轮没有完成：本轮使用的资料已经更新，已阻止旧结果写入。')) {
    return '上次生成时资料有更新，回复正文没有保存下来。你的要求还在，点击「重新生成」即可重试。';
  }
  return text;
}
