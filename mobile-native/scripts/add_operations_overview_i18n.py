#!/usr/bin/env python3
"""One-shot catalog writer for the Operations executive overview.

Adds `premium.privateOffice.operations.overview` to every locale's
`extended.json`. Additive: an existing key is left alone rather than
overwritten, so re-running is a no-op and a later human retranslation is never
clobbered. `$version` is not touched — bumping it here would invalidate every
catalog at launch for the sake of twenty-five strings.

Written as a script rather than eleven hand edits for the same reason as
`add_private_office_i18n.py`: placeholder parity across eleven locales is a CI
failure, and a hand edit that drops `{{total}}` in one file is exactly the kind
of mistake that survives review and ships.

The record-type and status labels are deliberately absent. They already exist
under `operations.views` and `operations.status`, and a second set would be a
second vocabulary to keep in step with the server's.
"""
import json
import os

ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src", "i18n", "catalogs")

# Each locale supplies the same twenty-five keys. The reason labels are the
# server's `REASON_RANK` names — they are rendered as chips beside a row, so
# they are phrased as a condition ("Overdue"), not as an instruction.
OVERVIEW = {
    "en": {
        "heading": "Overview",
        "loading": "Loading your overview…",
        "unavailable": "Your overview could not be loaded. These are not zeros.",
        "needsAttention": "Needs attention",
        "dueToday": "Due today",
        "overdue": "Overdue",
        "pendingDecisions": "Pending decisions",
        "dueThisWeek": "Due this week",
        "highRisks": "High risks",
        "openRequests": "Open requests",
        "awaitingResponse": "Awaiting your response",
        "opportunities": "Opportunities",
        "recentlyCompleted": "Recently completed",
        "expiringOpportunities": "Expiring opportunities",
        "recentWindow": "Last {{days}} days",
        "queue": "Attention queue",
        "queueEmpty": "Nothing needs your attention.",
        "truncated": "Showing {{shown}} of {{total}}.",
        "notTracked": "Not tracked",
        "reason": {
            "OVERDUE": "Overdue",
            "HIGH_RISK": "High risk",
            "DUE_SOON": "Due soon",
            "RESPONSE_REQUIRED": "Needs your reply",
            "MISSING_REQUIRED_CONTEXT": "Needs review",
            "DECISION_REQUIRED": "Decision open",
            "BLOCKED": "Waiting on provider",
        },
    },
    "es": {
        "heading": "Resumen",
        "loading": "Cargando tu resumen…",
        "unavailable": "No se pudo cargar tu resumen. Esto no son ceros.",
        "needsAttention": "Requiere atención",
        "dueToday": "Vence hoy",
        "overdue": "Vencido",
        "pendingDecisions": "Decisiones pendientes",
        "dueThisWeek": "Vence esta semana",
        "highRisks": "Riesgos altos",
        "openRequests": "Solicitudes abiertas",
        "awaitingResponse": "Esperando tu respuesta",
        "opportunities": "Oportunidades",
        "recentlyCompleted": "Completado recientemente",
        "expiringOpportunities": "Oportunidades por expirar",
        "recentWindow": "Últimos {{days}} días",
        "queue": "Cola de atención",
        "queueEmpty": "Nada requiere tu atención.",
        "truncated": "Mostrando {{shown}} de {{total}}.",
        "notTracked": "No se registra",
        "reason": {
            "OVERDUE": "Vencido",
            "HIGH_RISK": "Riesgo alto",
            "DUE_SOON": "Vence pronto",
            "RESPONSE_REQUIRED": "Necesita tu respuesta",
            "MISSING_REQUIRED_CONTEXT": "Necesita revisión",
            "DECISION_REQUIRED": "Decisión abierta",
            "BLOCKED": "Esperando al proveedor",
        },
    },
    "fr": {
        "heading": "Aperçu",
        "loading": "Chargement de votre aperçu…",
        "unavailable": "Votre aperçu n'a pas pu être chargé. Ce ne sont pas des zéros.",
        "needsAttention": "Requiert votre attention",
        "dueToday": "À échéance aujourd'hui",
        "overdue": "En retard",
        "pendingDecisions": "Décisions en attente",
        "dueThisWeek": "À échéance cette semaine",
        "highRisks": "Risques élevés",
        "openRequests": "Demandes ouvertes",
        "awaitingResponse": "En attente de votre réponse",
        "opportunities": "Opportunités",
        "recentlyCompleted": "Terminé récemment",
        "expiringOpportunities": "Opportunités qui expirent",
        "recentWindow": "{{days}} derniers jours",
        "queue": "File d'attention",
        "queueEmpty": "Rien ne requiert votre attention.",
        "truncated": "Affichage de {{shown}} sur {{total}}.",
        "notTracked": "Non suivi",
        "reason": {
            "OVERDUE": "En retard",
            "HIGH_RISK": "Risque élevé",
            "DUE_SOON": "Échéance proche",
            "RESPONSE_REQUIRED": "Votre réponse est attendue",
            "MISSING_REQUIRED_CONTEXT": "À examiner",
            "DECISION_REQUIRED": "Décision ouverte",
            "BLOCKED": "En attente du prestataire",
        },
    },
    "ht": {
        "heading": "Apèsi",
        "loading": "N ap chaje apèsi w la…",
        "unavailable": "Nou pa t kapab chaje apèsi w la. Sa yo se pa zewo.",
        "needsAttention": "Bezwen atansyon",
        "dueToday": "Delè jodi a",
        "overdue": "An reta",
        "pendingDecisions": "Desizyon k ap tann",
        "dueThisWeek": "Delè semèn sa a",
        "highRisks": "Gwo risk",
        "openRequests": "Demann ouvè",
        "awaitingResponse": "K ap tann repons ou",
        "opportunities": "Opòtinite",
        "recentlyCompleted": "Fèk fini",
        "expiringOpportunities": "Opòtinite k ap ekspire",
        "recentWindow": "Dènye {{days}} jou",
        "queue": "Liy atansyon",
        "queueEmpty": "Anyen pa bezwen atansyon w.",
        "truncated": "Montre {{shown}} sou {{total}}.",
        "notTracked": "Pa swiv",
        "reason": {
            "OVERDUE": "An reta",
            "HIGH_RISK": "Gwo risk",
            "DUE_SOON": "Delè pwoche",
            "RESPONSE_REQUIRED": "Bezwen repons ou",
            "MISSING_REQUIRED_CONTEXT": "Bezwen revizyon",
            "DECISION_REQUIRED": "Desizyon ouvè",
            "BLOCKED": "K ap tann founisè a",
        },
    },
    "pt": {
        "heading": "Visão geral",
        "loading": "Carregando sua visão geral…",
        "unavailable": "Não foi possível carregar sua visão geral. Isto não são zeros.",
        "needsAttention": "Requer atenção",
        "dueToday": "Vence hoje",
        "overdue": "Atrasado",
        "pendingDecisions": "Decisões pendentes",
        "dueThisWeek": "Vence esta semana",
        "highRisks": "Riscos altos",
        "openRequests": "Solicitações abertas",
        "awaitingResponse": "Aguardando sua resposta",
        "opportunities": "Oportunidades",
        "recentlyCompleted": "Concluído recentemente",
        "expiringOpportunities": "Oportunidades a expirar",
        "recentWindow": "Últimos {{days}} dias",
        "queue": "Fila de atenção",
        "queueEmpty": "Nada requer sua atenção.",
        "truncated": "Mostrando {{shown}} de {{total}}.",
        "notTracked": "Não registrado",
        "reason": {
            "OVERDUE": "Atrasado",
            "HIGH_RISK": "Risco alto",
            "DUE_SOON": "Vence em breve",
            "RESPONSE_REQUIRED": "Precisa da sua resposta",
            "MISSING_REQUIRED_CONTEXT": "Precisa de revisão",
            "DECISION_REQUIRED": "Decisão em aberto",
            "BLOCKED": "Aguardando o prestador",
        },
    },
    "de": {
        "heading": "Übersicht",
        "loading": "Übersicht wird geladen…",
        "unavailable": "Ihre Übersicht konnte nicht geladen werden. Das sind keine Nullen.",
        "needsAttention": "Erfordert Aufmerksamkeit",
        "dueToday": "Heute fällig",
        "overdue": "Überfällig",
        "pendingDecisions": "Offene Entscheidungen",
        "dueThisWeek": "Diese Woche fällig",
        "highRisks": "Hohe Risiken",
        "openRequests": "Offene Anfragen",
        "awaitingResponse": "Wartet auf Ihre Antwort",
        "opportunities": "Gelegenheiten",
        "recentlyCompleted": "Kürzlich erledigt",
        "expiringOpportunities": "Auslaufende Gelegenheiten",
        "recentWindow": "Letzte {{days}} Tage",
        "queue": "Aufmerksamkeitsliste",
        "queueEmpty": "Nichts erfordert Ihre Aufmerksamkeit.",
        "truncated": "{{shown}} von {{total}} werden angezeigt.",
        "notTracked": "Nicht erfasst",
        "reason": {
            "OVERDUE": "Überfällig",
            "HIGH_RISK": "Hohes Risiko",
            "DUE_SOON": "Bald fällig",
            "RESPONSE_REQUIRED": "Ihre Antwort nötig",
            "MISSING_REQUIRED_CONTEXT": "Prüfung nötig",
            "DECISION_REQUIRED": "Entscheidung offen",
            "BLOCKED": "Wartet auf Anbieter",
        },
    },
    "ar": {
        "heading": "نظرة عامة",
        "loading": "جارٍ تحميل نظرتك العامة…",
        "unavailable": "تعذّر تحميل نظرتك العامة. هذه ليست أصفارًا.",
        "needsAttention": "يحتاج إلى انتباهك",
        "dueToday": "مستحق اليوم",
        "overdue": "متأخر",
        "pendingDecisions": "قرارات معلّقة",
        "dueThisWeek": "مستحق هذا الأسبوع",
        "highRisks": "مخاطر عالية",
        "openRequests": "طلبات مفتوحة",
        "awaitingResponse": "بانتظار ردّك",
        "opportunities": "الفرص",
        "recentlyCompleted": "أُنجز مؤخرًا",
        "expiringOpportunities": "فرص تنتهي قريبًا",
        "recentWindow": "آخر {{days}} يومًا",
        "queue": "قائمة الانتباه",
        "queueEmpty": "لا شيء يحتاج إلى انتباهك.",
        "truncated": "عرض {{shown}} من {{total}}.",
        "notTracked": "غير متتبَّع",
        "reason": {
            "OVERDUE": "متأخر",
            "HIGH_RISK": "خطر عالٍ",
            "DUE_SOON": "يستحق قريبًا",
            "RESPONSE_REQUIRED": "يحتاج إلى ردّك",
            "MISSING_REQUIRED_CONTEXT": "يحتاج إلى مراجعة",
            "DECISION_REQUIRED": "قرار مفتوح",
            "BLOCKED": "بانتظار المزوّد",
        },
    },
    "hi": {
        "heading": "अवलोकन",
        "loading": "आपका अवलोकन लोड हो रहा है…",
        "unavailable": "आपका अवलोकन लोड नहीं हो सका। ये शून्य नहीं हैं।",
        "needsAttention": "ध्यान चाहिए",
        "dueToday": "आज देय",
        "overdue": "अतिदेय",
        "pendingDecisions": "लंबित निर्णय",
        "dueThisWeek": "इस सप्ताह देय",
        "highRisks": "उच्च जोखिम",
        "openRequests": "खुले अनुरोध",
        "awaitingResponse": "आपके उत्तर की प्रतीक्षा",
        "opportunities": "अवसर",
        "recentlyCompleted": "हाल ही में पूर्ण",
        "expiringOpportunities": "समाप्त होते अवसर",
        "recentWindow": "पिछले {{days}} दिन",
        "queue": "ध्यान सूची",
        "queueEmpty": "कुछ भी आपके ध्यान की प्रतीक्षा में नहीं है।",
        "truncated": "{{total}} में से {{shown}} दिखाए जा रहे हैं।",
        "notTracked": "ट्रैक नहीं किया गया",
        "reason": {
            "OVERDUE": "अतिदेय",
            "HIGH_RISK": "उच्च जोखिम",
            "DUE_SOON": "जल्द देय",
            "RESPONSE_REQUIRED": "आपके उत्तर की आवश्यकता",
            "MISSING_REQUIRED_CONTEXT": "समीक्षा आवश्यक",
            "DECISION_REQUIRED": "निर्णय शेष",
            "BLOCKED": "प्रदाता की प्रतीक्षा",
        },
    },
    "ja": {
        "heading": "概要",
        "loading": "概要を読み込んでいます…",
        "unavailable": "概要を読み込めませんでした。これはゼロではありません。",
        "needsAttention": "対応が必要",
        "dueToday": "本日期限",
        "overdue": "期限超過",
        "pendingDecisions": "保留中の決定",
        "dueThisWeek": "今週期限",
        "highRisks": "高いリスク",
        "openRequests": "未完了の依頼",
        "awaitingResponse": "あなたの返答待ち",
        "opportunities": "機会",
        "recentlyCompleted": "最近完了",
        "expiringOpportunities": "期限切れが近い機会",
        "recentWindow": "直近{{days}}日",
        "queue": "対応キュー",
        "queueEmpty": "対応が必要なものはありません。",
        "truncated": "{{total}}件中{{shown}}件を表示。",
        "notTracked": "未追跡",
        "reason": {
            "OVERDUE": "期限超過",
            "HIGH_RISK": "高リスク",
            "DUE_SOON": "期限間近",
            "RESPONSE_REQUIRED": "返答が必要",
            "MISSING_REQUIRED_CONTEXT": "確認が必要",
            "DECISION_REQUIRED": "決定が未了",
            "BLOCKED": "提供者待ち",
        },
    },
    "ko": {
        "heading": "개요",
        "loading": "개요를 불러오는 중…",
        "unavailable": "개요를 불러오지 못했습니다. 이것은 0이 아닙니다.",
        "needsAttention": "주의 필요",
        "dueToday": "오늘 마감",
        "overdue": "기한 초과",
        "pendingDecisions": "대기 중인 결정",
        "dueThisWeek": "이번 주 마감",
        "highRisks": "높은 위험",
        "openRequests": "진행 중인 요청",
        "awaitingResponse": "회신 대기 중",
        "opportunities": "기회",
        "recentlyCompleted": "최근 완료",
        "expiringOpportunities": "만료 예정 기회",
        "recentWindow": "최근 {{days}}일",
        "queue": "주의 목록",
        "queueEmpty": "주의가 필요한 항목이 없습니다.",
        "truncated": "{{total}}개 중 {{shown}}개 표시.",
        "notTracked": "추적하지 않음",
        "reason": {
            "OVERDUE": "기한 초과",
            "HIGH_RISK": "높은 위험",
            "DUE_SOON": "마감 임박",
            "RESPONSE_REQUIRED": "회신 필요",
            "MISSING_REQUIRED_CONTEXT": "검토 필요",
            "DECISION_REQUIRED": "결정 미완",
            "BLOCKED": "제공자 대기",
        },
    },
    "zh": {
        "heading": "概览",
        "loading": "正在加载您的概览…",
        "unavailable": "无法加载您的概览。这些不是零。",
        "needsAttention": "需要处理",
        "dueToday": "今日到期",
        "overdue": "已逾期",
        "pendingDecisions": "待定决策",
        "dueThisWeek": "本周到期",
        "highRisks": "高风险",
        "openRequests": "进行中的请求",
        "awaitingResponse": "等待您的答复",
        "opportunities": "机会",
        "recentlyCompleted": "最近完成",
        "expiringOpportunities": "即将到期的机会",
        "recentWindow": "最近 {{days}} 天",
        "queue": "待处理队列",
        "queueEmpty": "目前没有需要您处理的事项。",
        "truncated": "显示 {{total}} 项中的 {{shown}} 项。",
        "notTracked": "未跟踪",
        "reason": {
            "OVERDUE": "已逾期",
            "HIGH_RISK": "高风险",
            "DUE_SOON": "即将到期",
            "RESPONSE_REQUIRED": "需要您答复",
            "MISSING_REQUIRED_CONTEXT": "需要复核",
            "DECISION_REQUIRED": "决策未定",
            "BLOCKED": "等待服务方",
        },
    },
}


def merge(target: dict, addition: dict) -> int:
    """Add missing keys only. Returns how many strings were written."""
    written = 0
    for key, value in addition.items():
        if isinstance(value, dict):
            branch = target.setdefault(key, {})
            if not isinstance(branch, dict):
                raise SystemExit(f"refusing to overwrite non-object at {key}")
            written += merge(branch, value)
        elif key not in target:
            target[key] = value
            written += 1
    return written


def main() -> int:
    total = 0
    for locale, strings in OVERVIEW.items():
        path = os.path.join(ROOT, locale, "extended.json")
        with open(path, encoding="utf-8") as handle:
            bundle = json.load(handle)
        ops = (bundle.setdefault("premium", {})
                     .setdefault("privateOffice", {})
                     .setdefault("operations", {}))
        written = merge(ops, {"overview": strings})
        if written:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(bundle, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        print(f"{locale}: {written} string(s) added")
        total += written
    print(f"total {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
