// Selected source material follows the benchmark route. A direct tier instruction
// can correct an old session's route; questions and quotations do not select a tier.
export function requestedStudioRoute(session, message) {
  if (session.skillId !== 'xuanti') return session.routeId;
  const text = String(message).replace(/```[\s\S]*?```|`[^`]*`|"[^"]*"|'[^']*'|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』/gu, '');
  const clauses = text.split(/[。；;，,\n]/u);
  const match = clauses.map((clause) => clause.match(/^\s*(?:(?:我选|选择|改用|改为|改成|切换到|切到|选|用|按|走)\s*)?(?:对标选题(?:法)?[\s：:]*)?(?:第)?([一二三123１２３])\s*档\s*[！!]?\s*$/u)).filter(Boolean).at(-1);
  const explicitBenchmark = clauses.some((clause) => /^\s*(?:(?:用|按|走|改用|改为)\s*)?对标选题(?:法)?\s*[！!]?\s*$/u.test(clause)) || Boolean(match?.[0].includes('对标选题'));
  const benchmark = session.routeId?.startsWith('benchmark-tier-') || session.source || session.reference || explicitBenchmark;
  if (!benchmark) return session.routeId;
  const fallback = session.routeId?.startsWith('benchmark-tier-') ? session.routeId : 'benchmark-tier-1';
  if (!match) return fallback;
  const tier = { 一: 1, 二: 2, 三: 3, '１': 1, '２': 2, '３': 3 }[match[1]] ?? Number(match[1]);
  return `benchmark-tier-${tier}`;
}
