/**
 * SK AI OnlyOffice Plugin — code.js
 *
 * auth-gateway /api/v1/ai/chat/completions (Lane A) 를 호출하는
 * 자체 작성 플러그인. AGPL 리스크 회피를 위해 공식 OO AI 플러그인 fork 없이
 * OnlyOffice Plugin SDK(window.Asc.plugin)를 직접 사용.
 *
 * 4개 기능:
 *   summarize  — 선택 영역 요약
 *   translate  — 한↔영 번역
 *   grammar    — 문법 교정
 *   report     — 보고서 초안 생성
 *
 * 인증: fetch credentials:'include' → bedrock_jwt 쿠키 자동 전달
 *       (OO와 auth-gateway 모두 claude.skons.net 단일 도메인)
 *
 * URL: window.SKAI_CONFIG.aiEndpoint (ConfigMap 주입) 또는
 *      '/api/v1/ai/chat/completions' (기본값)
 *
 * 설계: docs/plans/2026-04-12-onlyoffice-ai-integration-design.md § D3
 */
(function (window, undefined) {
  'use strict';

  /* ── 설정 ────────────────────────────────────────────────────────────── */

  /** ConfigMap이 주입한 config.js 또는 기본값 */
  var CFG = window.SKAI_CONFIG || {};
  var ENDPOINT = CFG.aiEndpoint || '/api/v1/ai/chat/completions';
  var MODEL    = CFG.model      || 'claude-sonnet-4-6';
  var MAX_TOKENS = 2048;

  /** 액션별 메뉴 레이블 */
  var ACTION_LABELS = {
    summarize : '선택 영역 요약',
    translate : '한↔영 번역',
    grammar   : '문법 교정',
    report    : '보고서 초안 생성'
  };

  /**
   * 액션별 시스템 프롬프트.
   * 한국어 기준으로 작성. Bedrock Claude Sonnet 4.6이 처리.
   */
  var SYSTEM_PROMPTS = {
    summarize: [
      '당신은 문서 요약 전문가입니다.',
      '주어진 텍스트를 한국어로 간결하게 요약하세요.',
      '핵심 내용을 3~5문장으로 정리하고, 원문의 중요 수치나 고유명사는 그대로 보존하세요.',
      '요약문만 반환하고, 추가 설명은 생략하세요.'
    ].join(' '),

    translate: [
      '당신은 전문 번역가입니다.',
      '주어진 텍스트의 언어를 감지하여: 한국어이면 자연스러운 영어로, 영어이면 자연스러운 한국어로 번역하세요.',
      '번역된 텍스트만 반환하고, 언어 감지 결과나 추가 설명은 생략하세요.',
      '전문 용어와 고유명사는 문맥에 맞게 처리하세요.'
    ].join(' '),

    grammar: [
      '당신은 문법 및 맞춤법 교정 전문가입니다.',
      '주어진 텍스트의 문법 오류, 맞춤법 오류, 어색한 표현을 교정하세요.',
      '교정된 전체 텍스트만 반환하세요.',
      '원문의 의미와 어조는 최대한 보존하고, 불필요한 변경은 피하세요.'
    ].join(' '),

    report: [
      '당신은 기업 보고서 작성 전문가입니다.',
      '주어진 내용을 바탕으로 팀 주간 보고서 초안을 작성하세요.',
      '다음 구조를 따르세요: 1) 주요 성과 2) 진행 중인 업무 3) 이슈 및 리스크 4) 다음 주 계획.',
      '각 항목은 명확하고 간결한 bullet point 형식으로 작성하세요.',
      '전문적이고 보고서에 적합한 어조를 사용하세요.'
    ].join(' ')
  };

  /* ── 상태 ────────────────────────────────────────────────────────────── */

  var _currentAction = 'summarize';
  var _aiResult      = null;       // 삽입 버튼 클릭 시 사용
  var _tokenInfo     = null;       // 토큰 사용량 (표시용)

  /* ── OO Plugin 진입점 ─────────────────────────────────────────────────── */

  /**
   * init(data): OO가 플러그인을 열 때 호출.
   * data = config.json variations[n].initData (액션 식별자)
   */
  window.Asc.plugin.init = function (data) {
    _currentAction = (data && ACTION_LABELS[data]) ? data : 'summarize';
    _aiResult      = null;
    _tokenInfo     = null;

    _updateTitle(_currentAction);
    _setStatus('선택한 텍스트를 불러오는 중...', 'loading');

    // 에디터의 현재 선택 텍스트를 가져온다.
    // Numbering:false → 번호 목록 번호 제외
    // ParaSeparator:'\n' → 단락 구분을 줄바꿈으로 변환
    window.Asc.plugin.executeMethod(
      'GetSelectedText',
      [{ Numbering: false, Math: false, TableCellSeparator: '\n', ParaSeparator: '\n' }],
      function (selectedText) {
        var text = (selectedText || '').replace(/\r/g, '').trim();
        if (!text) {
          _setStatus(
            '텍스트를 선택한 후 플러그인을 사용하세요.\n(선택 없이 실행하면 전체 문서 내용을 가져올 수 없습니다.)',
            'error'
          );
          _showHint('에디터에서 원하는 텍스트를 드래그하여 선택하고 다시 실행하세요.');
          return;
        }
        _callAI(_currentAction, text);
      }
    );
  };

  /**
   * button(id): OO 프레임워크 버튼 클릭 시 호출.
   * id=0 → 삽입 / 교정본 삽입 / 초안 삽입 (primary)
   * id=1 → 닫기
   */
  window.Asc.plugin.button = function (id) {
    if (id === 0 && _aiResult) {
      _insertResult(_aiResult);
    }
    // 삽입 후 또는 닫기 버튼: 플러그인 창 닫기
    window.Asc.plugin.executeCommand('close', '');
  };

  /* ── AI 호출 ────────────────────────────────────────────────────────── */

  function _callAI(action, selectedText) {
    _setStatus('AI가 처리 중입니다...', 'loading');

    var systemPrompt = SYSTEM_PROMPTS[action] || SYSTEM_PROMPTS.summarize;
    var requestBody = JSON.stringify({
      model   : MODEL,
      messages: [
        { role: 'user', content: systemPrompt + '\n\n---\n\n' + selectedText }
      ],
      max_tokens : MAX_TOKENS,
      stream     : false
    });

    fetch(ENDPOINT, {
      method     : 'POST',
      credentials: 'include',   // bedrock_jwt 쿠키 자동 전달
      headers    : {
        'Content-Type': 'application/json'
      },
      body: requestBody
    })
    .then(function (res) {
      if (res.status === 401) {
        throw _makeError('인증이 만료되었습니다. 페이지를 새로 고침하여 로그인해 주세요.', 401);
      }
      if (res.status === 429) {
        throw _makeError('사용량 한도에 도달했습니다. 잠시 후 다시 시도하세요.', 429);
      }
      if (!res.ok) {
        return res.json().then(function (err) {
          var msg = (err && err.error && err.error.message) || ('API 오류 (' + res.status + ')');
          throw _makeError(msg, res.status);
        }, function () {
          throw _makeError('API 오류 (' + res.status + ')', res.status);
        });
      }
      return res.json();
    })
    .then(function (data) {
      var choice  = data.choices && data.choices[0];
      var message = choice && choice.message;
      var result  = message && message.content;

      if (!result) {
        throw _makeError('응답에서 텍스트를 추출할 수 없습니다.', 0);
      }

      _aiResult = result.trim();

      // 토큰 사용량 표시 (있을 때만)
      if (data.usage) {
        _tokenInfo = '입력 ' + (data.usage.prompt_tokens || 0)
          + ' / 출력 ' + (data.usage.completion_tokens || 0) + ' 토큰';
      }

      _showResult(_aiResult);
    })
    .catch(function (err) {
      var msg = err.message || '알 수 없는 오류가 발생했습니다.';
      _setStatus(msg, 'error');
      _showHint('닫기 버튼을 눌러 플러그인을 종료하세요.');
    });
  }

  /* ── 결과 삽입 ───────────────────────────────────────────────────────── */

  /**
   * 선택 영역에 AI 결과를 HTML로 붙여넣는다.
   * PasteHtml은 현재 선택 범위를 교체하는 효과를 낸다.
   * 단락 구분(\n\n)은 <p>로, 줄바꿈(\n)은 <br>로 변환.
   */
  function _insertResult(text) {
    var html = text
      .split('\n\n')
      .map(function (para) {
        return '<p>' + _escapeHtml(para).replace(/\n/g, '<br>') + '</p>';
      })
      .join('');

    window.Asc.plugin.executeMethod('PasteHtml', [html]);
  }

  /* ── UI 헬퍼 ─────────────────────────────────────────────────────────── */

  function _updateTitle(action) {
    var el = document.getElementById('sk-ai-title');
    if (el) el.textContent = ACTION_LABELS[action] || 'SK AI';
  }

  function _setStatus(msg, type) {
    var statusEl  = document.getElementById('sk-ai-status');
    var spinnerEl = document.getElementById('sk-ai-spinner');

    if (statusEl) {
      statusEl.textContent = msg;
      statusEl.className = type === 'error' ? 'error' : (type === 'success' ? 'success' : '');
    }

    if (spinnerEl) {
      if (type === 'loading') {
        spinnerEl.classList.add('visible');
      } else {
        spinnerEl.classList.remove('visible');
      }
    }

    // 결과 영역 숨기기 (에러/로딩 중)
    if (type !== 'success') {
      var wrapper = document.getElementById('sk-ai-result-wrapper');
      if (wrapper) wrapper.style.display = 'none';
    }
  }

  function _showResult(text) {
    var statusEl  = document.getElementById('sk-ai-status');
    var spinnerEl = document.getElementById('sk-ai-spinner');
    var wrapper   = document.getElementById('sk-ai-result-wrapper');
    var resultEl  = document.getElementById('sk-ai-result');
    var tokenEl   = document.getElementById('sk-ai-token-info');
    var hintEl    = document.getElementById('sk-ai-hint');

    if (spinnerEl) spinnerEl.classList.remove('visible');
    if (statusEl) {
      statusEl.textContent = '완료! 결과를 확인하고 \'삽입\' 버튼을 누르세요.';
      statusEl.className = 'success';
    }
    if (wrapper) wrapper.style.display = 'flex';
    if (resultEl) {
      resultEl.value = text;
      resultEl.removeAttribute('readonly');  // 수동 편집 허용
    }
    if (tokenEl && _tokenInfo) {
      tokenEl.textContent = _tokenInfo;
    }
    if (hintEl) hintEl.textContent = '';
  }

  function _showHint(msg) {
    var el = document.getElementById('sk-ai-hint');
    if (el) el.textContent = msg;
  }

  /* ── 유틸리티 ────────────────────────────────────────────────────────── */

  function _makeError(message, status) {
    var e = new Error(message);
    e.status = status;
    return e;
  }

  function _escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

})(window, undefined);
