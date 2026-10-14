// Claude.ai 대화 다운로드 스크립트 v3
// 사용법: claude.ai에서 F12 → Console → 이 코드 붙여넣기
//         → claude_conversations_<날짜>.json 다운로드됨
//         → python unpack_claude_json.py <그 파일> 로 conversations/*.md 생성
//
// 2026-07 부터 이 스크립트가 유일한 인제스트 경로: claude.ai 가 /api 를
// Cloudflare 봇 챌린지로 막아 sessionKey 쿠키 방식 외부 HTTP 호출은 403.
// 로그인된 브라우저 안(same-origin)에서는 정상 동작한다.

(async () => {
  const orgId = document.cookie.match(/lastActiveOrg=([^;]+)/)?.[1];
  if (!orgId) { console.error('❌ orgId를 찾을 수 없습니다.'); return; }

  const BASE = `/api/organizations/${orgId}/chat_conversations`;
  const LIMIT = 2000;  // 전체 대화 상한 (2026-07 기준 계정 전체 638개)

  // 1. 대화 목록
  console.log('📋 대화 목록 가져오는 중...');
  let allConvos = [];
  let offset = 0;
  while (allConvos.length < LIMIT) {
    const r = await fetch(`${BASE}?limit=50&offset=${offset}`);
    if (!r.ok) { console.error(`❌ API 오류: ${r.status}`); return; }
    const batch = await r.json();
    if (!batch.length) break;
    allConvos.push(...batch);
    offset += 50;
    if (batch.length < 50) break;
  }
  allConvos = allConvos.slice(0, LIMIT);
  console.log(`✅ 총 ${allConvos.length}개 대화 발견`);

  // 2. 각 대화 다운로드 - 먼저 1개만 구조 확인
  const testConv = allConvos[0];
  const testR = await fetch(`${BASE}/${testConv.uuid}`);
  const testData = await testR.json();
  console.log('📋 API 응답 키:', Object.keys(testData));
  console.log('📋 첫 대화 구조 샘플:', JSON.stringify(testData).slice(0, 500));

  // chat_messages 외에 다른 키 이름 탐색
  const msgKey = Object.keys(testData).find(k =>
    Array.isArray(testData[k]) && testData[k].length > 0 &&
    testData[k][0] && (testData[k][0].sender || testData[k][0].role || testData[k][0].type)
  ) || 'chat_messages';
  console.log(`📋 메시지 키: "${msgKey}", 메시지 수: ${(testData[msgKey]||[]).length}`);

  // 첫 메시지 구조 확인
  if (testData[msgKey] && testData[msgKey][0]) {
    console.log('📋 첫 메시지 구조:', JSON.stringify(testData[msgKey][0]).slice(0, 300));
  }

  // 3. 전체 다운로드
  console.log('\n📥 다운로드 시작...');
  const mdFiles = [];
  let success = 0, fail = 0;

  for (let i = 0; i < allConvos.length; i++) {
    const conv = allConvos[i];
    const convId = conv.uuid;
    const name = conv.name || '제목없음';
    const created = (conv.created_at || '').slice(0, 10);

    try {
      const r = await fetch(`${BASE}/${convId}`);
      if (!r.ok) { fail++; continue; }
      const data = await r.json();

      let md = `# ${name}\n\n- **생성일**: ${created}\n- ID: ${convId}\n\n`;

      const messages = data[msgKey] || [];
      for (const msg of messages) {
        // sender 또는 role 필드 탐색
        const sender = msg.sender || msg.role || '';

        // content/text 추출 - 여러 형식 지원
        let text = '';
        const content = msg.content || msg.text || msg.body || '';
        if (Array.isArray(content)) {
          text = content
            .map(p => {
              if (typeof p === 'string') return p;
              if (p && p.text) return p.text;
              if (p && p.content) return p.content;
              return '';
            })
            .filter(t => t)
            .join('\n');
        } else if (typeof content === 'string') {
          text = content;
        }
        if (!text.trim()) continue;

        if (sender === 'human' || sender === 'user') {
          md += `## 👤 User\n\n${text.trim()}\n\n`;
        } else if (sender === 'assistant') {
          md += `## 🤖 Assistant\n\n${text.trim()}\n\n`;
        }
      }

      const safeName = name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 80);
      mdFiles.push({ filename: `${created}_${safeName}.md`, content: md });
      success++;
      if ((i + 1) % 10 === 0) console.log(`  진행: ${i + 1}/${allConvos.length}`);
    } catch (e) { fail++; }
  }

  console.log(`\n✅ 변환 완료: 성공 ${success}, 실패 ${fail}`);

  const blob = new Blob([JSON.stringify(mdFiles, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `claude_conversations_${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  console.log(`📦 다운로드 완료! 파일: ${a.download}`);
})();
