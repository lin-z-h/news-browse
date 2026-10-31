import verify_guard
from verify_guard import EXIT_CHALLENGE, detect_challenge, substantive_content


def _article_about_captcha() -> str:
    """内容厚实（>40 行且 >2000 字符）、正文讨论 CAPTCHA 的正常归档文章。"""
    lines = []
    for i in range(50):
        lines.append(
            f"paragraph {i:02d} the quick brown fox jumps over the lazy dog "
            f"and the newspaper printed a story about rating systems and "
            f"human verification research online today"
        )
    return "<html><head><title>Why CAPTCHAs still matter</title></head><body>" + "\n".join(lines) + "</body></html>"


CLOUDFLARE_PAGE = (
    "<html><head><title>Just a moment...</title></head><body>"
    "Please wait while we verify your browser. cf_chl_opt=1 "
    "challenge-platform/scripts/jsd/main.js</body></html>"
)

THIN_CAPTCHA_PAGE = (
    "<html><head><title>Security Check</title></head>"
    "<body>Complete the captcha to continue.</body></html>"
)


def test_exit_code_is_75():
    assert EXIT_CHALLENGE == 75
    assert verify_guard.EXIT_CHALLENGE == 75


def test_cloudflare_title_detected():
    ch = detect_challenge(CLOUDFLARE_PAGE)
    assert ch is not None and ch.kind == "cloudflare"


def test_thin_page_with_captcha_word_detected():
    ch = detect_challenge(THIN_CAPTCHA_PAGE)
    assert ch is not None and ch.kind == "captcha"


def test_substantive_article_not_flagged():
    body = _article_about_captcha()
    assert substantive_content(body)
    assert detect_challenge(body) is None


def test_strong_infra_marker_always_flagged():
    # 即使内容厚实，challenge-platform 等基础设施标记也视为验证页
    body = _article_about_captcha() + " challenge-platform"
    assert detect_challenge(body) is not None


def test_substantive_thresholds():
    assert not substantive_content("")                      # 空
    assert not substantive_content("x" * 5000)              # 单行
    assert not substantive_content("\n".join(["ab"] * 100))  # 行多但字数少
    body = "\n".join("x" * 60 for _ in range(45))           # 行数、字数均达标
    assert substantive_content(body)


def test_empty_body_no_challenge():
    assert detect_challenge("") is None


def test_sitekey_extraction():
    body = '<div class="g-recaptcha" data-sitekey="6LcABCDabcdEFGH1234567890-_xyz"></div>'
    ch = detect_challenge(THIN_CAPTCHA_PAGE + body)
    assert ch is not None and ch.sitekey == "6LcABCDabcdEFGH1234567890-_xyz"


def test_recaptcha_word_in_thin_page():
    body = "<html><body>recaptcha verification required, enter the code</body></html>"
    ch = detect_challenge(body)
    assert ch is not None


def test_archive_today_rate_limit_page_detected():
    """archive.today 限速页内容很厚（59KB/265行），但含真实验证组件标记，
    必须判为验证页而不是被'实质内容'规则漏判。"""
    widget = (
        '<script src="https://www.google.com/recaptcha/api.js?hl=en&onload=onloadCallback"></script>'
        '<div class="g-recaptcha" data-sitekey="6LcXYZabcdefgh1234567890"></div>'
        "Please pass a security check to access this page."
    )
    body = _article_about_captcha() + widget
    assert substantive_content(body)
    ch = detect_challenge(body)
    assert ch is not None and ch.kind == "cloudflare"
    assert ch.sitekey == "6LcXYZabcdefgh1234567890"


def test_word_captcha_alone_in_thick_page_not_flagged():
    body = _article_about_captcha()  # 只提到 captcha 一词，无组件标记
    assert detect_challenge(body) is None
