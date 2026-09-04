#!/usr/bin/env python3
"""One-shot catalog writer for the Private Office second lock (passcode security).

Adds `common.screens.privateOfficeSecurity` to every locale's `core.json`, and
the `sections.security`, `security`, and `lock` subtrees to every locale's
`premium.privateOffice` in `extended.json`. Additive: an existing key is left
alone rather than overwritten, so re-running is a no-op and a later human
retranslation is never clobbered.

Same shape as `add_private_office_i18n.py`, and for the same reason: placeholder
parity across 11 locales is CI-checked, and one hand edit that drops
`{{digits}}` in one file fails the build. Placeholders used here: `{{digits}}`
(lock.setup.create.body, lock.policy), `{{seconds}}` (lock.cooldown),
`{{method}}` (security.biometric.label, lock.setup.biometric.title,
lock.setup.biometric.enable).
"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "i18n", "catalogs")

CORE = {
    "en": {"privateOfficeSecurity": "Office Security"},
    "es": {"privateOfficeSecurity": "Seguridad de la oficina"},
    "fr": {"privateOfficeSecurity": "Sécurité du bureau"},
    "ht": {"privateOfficeSecurity": "Sekirite biwo a"},
    "pt": {"privateOfficeSecurity": "Segurança do escritório"},
    "de": {"privateOfficeSecurity": "Büro-Sicherheit"},
    "ar": {"privateOfficeSecurity": "أمان المكتب"},
    "hi": {"privateOfficeSecurity": "कार्यालय सुरक्षा"},
    "ja": {"privateOfficeSecurity": "オフィスのセキュリティ"},
    "ko": {"privateOfficeSecurity": "오피스 보안"},
    "zh": {"privateOfficeSecurity": "办公室安全"},
}

EN = {
    "sections": {"security": "Security"},
    "security": {
        "title": "Office Security",
        "subtitle": "Control how your Private Office locks and unlocks.",
        "row": {
            "label": "Office Security",
            "hint": "Passcode, biometrics, and relock timing",
        },
        "relock": {
            "label": "Require passcode",
            "hint": "How soon the Office locks after you leave the app.",
            "immediate": "Immediately",
            "1m": "After 1 minute",
            "5m": "After 5 minutes",
            "15m": "After 15 minutes",
        },
        "biometric": {
            "label": "Unlock with {{method}}",
            "hint": "Your passcode stays required as a fallback.",
            "confirm": "Enter your Office passcode to confirm",
        },
        "change": {
            "label": "Change passcode",
            "current": "Current passcode",
            "new": "New passcode",
            "confirm": "Confirm new passcode",
            "submit": "Change passcode",
            "success": "Passcode changed. The Office is now locked on all devices.",
        },
        "lockNow": "Lock now",
        "lockNowHint": "Locks the Office on all your devices.",
    },
    "lock": {
        "checking": "Checking Office security…",
        "unavailable": {
            "title": "Office security unavailable",
            "body": "We couldn't reach the server to check the Office lock. Try again.",
        },
        "setup": {
            "intro": {
                "title": "Add a second lock",
                "body": "Your Private Office gets its own passcode, separate from your account password.",
                "start": "Set up passcode",
                "notNow": "Not now",
            },
            "create": {
                "title": "Create Office passcode",
                "body": "Use at least {{digits}} digits. You'll need it every time you open the Office.",
            },
            "confirm": {
                "title": "Confirm passcode",
                "body": "Enter the same passcode again.",
            },
            "continue": "Continue",
            "back": "Back",
            "mismatch": "Those passcodes don't match. Try again.",
            "biometric": {
                "title": "Unlock with {{method}}?",
                "body": "Open the Office faster. Your passcode remains the fallback.",
                "enable": "Use {{method}}",
                "skip": "Skip",
            },
        },
        "locked": {
            "title": "Office locked",
            "body": "Unlock the Private Office to see this content.",
        },
        "placeholder": "Office passcode",
        "unlock": "Unlock",
        "wrong": "Wrong passcode. Try again.",
        "cooldown": "Too many attempts. Try again in {{seconds}}s.",
        "policy": "Passcodes must be at least {{digits}} digits.",
        "error": "Something went wrong. Try again.",
        "forgot": "Forgot passcode?",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "Unlock your Private Office",
        "reset": {
            "title": "Reset Office passcode",
            "body": "Confirm your account password, then choose a new Office passcode. All devices will be locked.",
            "password": "Account password",
            "newPasscode": "New passcode",
            "confirmPasscode": "Confirm new passcode",
            "submit": "Reset passcode",
            "cancel": "Cancel",
            "failed": "Reset failed. Check your password and try again.",
        },
    },
}

ES = {
    "sections": {"security": "Seguridad"},
    "security": {
        "title": "Seguridad de la oficina",
        "subtitle": "Controla cómo se bloquea y desbloquea tu oficina privada.",
        "row": {
            "label": "Seguridad de la oficina",
            "hint": "Código, biometría y tiempo de bloqueo",
        },
        "relock": {
            "label": "Exigir código",
            "hint": "Cuánto tarda la oficina en bloquearse al salir de la app.",
            "immediate": "Inmediatamente",
            "1m": "Después de 1 minuto",
            "5m": "Después de 5 minutos",
            "15m": "Después de 15 minutos",
        },
        "biometric": {
            "label": "Desbloquear con {{method}}",
            "hint": "Tu código sigue siendo necesario como respaldo.",
            "confirm": "Introduce el código de tu oficina para confirmar",
        },
        "change": {
            "label": "Cambiar código",
            "current": "Código actual",
            "new": "Código nuevo",
            "confirm": "Confirmar código nuevo",
            "submit": "Cambiar código",
            "success": "Código cambiado. La oficina quedó bloqueada en todos los dispositivos.",
        },
        "lockNow": "Bloquear ahora",
        "lockNowHint": "Bloquea la oficina en todos tus dispositivos.",
    },
    "lock": {
        "checking": "Comprobando la seguridad de la oficina…",
        "unavailable": {
            "title": "Seguridad de la oficina no disponible",
            "body": "No pudimos contactar con el servidor para comprobar el bloqueo. Inténtalo de nuevo.",
        },
        "setup": {
            "intro": {
                "title": "Añade un segundo candado",
                "body": "Tu oficina privada tiene su propio código, separado de la contraseña de tu cuenta.",
                "start": "Configurar código",
                "notNow": "Ahora no",
            },
            "create": {
                "title": "Crea el código de la oficina",
                "body": "Usa al menos {{digits}} dígitos. Lo necesitarás cada vez que abras la oficina.",
            },
            "confirm": {
                "title": "Confirma el código",
                "body": "Introduce el mismo código otra vez.",
            },
            "continue": "Continuar",
            "back": "Atrás",
            "mismatch": "Los códigos no coinciden. Inténtalo de nuevo.",
            "biometric": {
                "title": "¿Desbloquear con {{method}}?",
                "body": "Abre la oficina más rápido. Tu código sigue siendo el respaldo.",
                "enable": "Usar {{method}}",
                "skip": "Omitir",
            },
        },
        "locked": {
            "title": "Oficina bloqueada",
            "body": "Desbloquea la oficina privada para ver este contenido.",
        },
        "placeholder": "Código de la oficina",
        "unlock": "Desbloquear",
        "wrong": "Código incorrecto. Inténtalo de nuevo.",
        "cooldown": "Demasiados intentos. Vuelve a intentarlo en {{seconds}} s.",
        "policy": "El código debe tener al menos {{digits}} dígitos.",
        "error": "Algo salió mal. Inténtalo de nuevo.",
        "forgot": "¿Olvidaste el código?",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "Desbloquea tu oficina privada",
        "reset": {
            "title": "Restablecer el código de la oficina",
            "body": "Confirma la contraseña de tu cuenta y elige un nuevo código. Se bloquearán todos los dispositivos.",
            "password": "Contraseña de la cuenta",
            "newPasscode": "Código nuevo",
            "confirmPasscode": "Confirmar código nuevo",
            "submit": "Restablecer código",
            "cancel": "Cancelar",
            "failed": "No se pudo restablecer. Revisa tu contraseña e inténtalo de nuevo.",
        },
    },
}

FR = {
    "sections": {"security": "Sécurité"},
    "security": {
        "title": "Sécurité du bureau",
        "subtitle": "Contrôlez comment votre bureau privé se verrouille et se déverrouille.",
        "row": {
            "label": "Sécurité du bureau",
            "hint": "Code, biométrie et délai de verrouillage",
        },
        "relock": {
            "label": "Exiger le code",
            "hint": "Délai avant que le bureau se verrouille après avoir quitté l'app.",
            "immediate": "Immédiatement",
            "1m": "Après 1 minute",
            "5m": "Après 5 minutes",
            "15m": "Après 15 minutes",
        },
        "biometric": {
            "label": "Déverrouiller avec {{method}}",
            "hint": "Votre code reste requis en secours.",
            "confirm": "Saisissez le code de votre bureau pour confirmer",
        },
        "change": {
            "label": "Changer le code",
            "current": "Code actuel",
            "new": "Nouveau code",
            "confirm": "Confirmer le nouveau code",
            "submit": "Changer le code",
            "success": "Code changé. Le bureau est verrouillé sur tous les appareils.",
        },
        "lockNow": "Verrouiller maintenant",
        "lockNowHint": "Verrouille le bureau sur tous vos appareils.",
    },
    "lock": {
        "checking": "Vérification de la sécurité du bureau…",
        "unavailable": {
            "title": "Sécurité du bureau indisponible",
            "body": "Impossible de joindre le serveur pour vérifier le verrou. Réessayez.",
        },
        "setup": {
            "intro": {
                "title": "Ajouter un second verrou",
                "body": "Votre bureau privé a son propre code, distinct du mot de passe de votre compte.",
                "start": "Configurer le code",
                "notNow": "Pas maintenant",
            },
            "create": {
                "title": "Créer le code du bureau",
                "body": "Utilisez au moins {{digits}} chiffres. Il sera demandé à chaque ouverture du bureau.",
            },
            "confirm": {
                "title": "Confirmer le code",
                "body": "Saisissez le même code à nouveau.",
            },
            "continue": "Continuer",
            "back": "Retour",
            "mismatch": "Les codes ne correspondent pas. Réessayez.",
            "biometric": {
                "title": "Déverrouiller avec {{method}} ?",
                "body": "Ouvrez le bureau plus vite. Votre code reste le secours.",
                "enable": "Utiliser {{method}}",
                "skip": "Ignorer",
            },
        },
        "locked": {
            "title": "Bureau verrouillé",
            "body": "Déverrouillez le bureau privé pour voir ce contenu.",
        },
        "placeholder": "Code du bureau",
        "unlock": "Déverrouiller",
        "wrong": "Code incorrect. Réessayez.",
        "cooldown": "Trop de tentatives. Réessayez dans {{seconds}} s.",
        "policy": "Le code doit contenir au moins {{digits}} chiffres.",
        "error": "Une erreur est survenue. Réessayez.",
        "forgot": "Code oublié ?",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "Déverrouillez votre bureau privé",
        "reset": {
            "title": "Réinitialiser le code du bureau",
            "body": "Confirmez le mot de passe de votre compte, puis choisissez un nouveau code. Tous les appareils seront verrouillés.",
            "password": "Mot de passe du compte",
            "newPasscode": "Nouveau code",
            "confirmPasscode": "Confirmer le nouveau code",
            "submit": "Réinitialiser le code",
            "cancel": "Annuler",
            "failed": "Échec de la réinitialisation. Vérifiez votre mot de passe et réessayez.",
        },
    },
}

HT = {
    "sections": {"security": "Sekirite"},
    "security": {
        "title": "Sekirite biwo a",
        "subtitle": "Kontwole kijan biwo prive w la bloke ak debloke.",
        "row": {
            "label": "Sekirite biwo a",
            "hint": "Kòd, byometri ak lè pou rebloke",
        },
        "relock": {
            "label": "Mande kòd la",
            "hint": "Konbyen tan anvan biwo a bloke apre ou kite app la.",
            "immediate": "Touswit",
            "1m": "Apre 1 minit",
            "5m": "Apre 5 minit",
            "15m": "Apre 15 minit",
        },
        "biometric": {
            "label": "Debloke ak {{method}}",
            "hint": "Kòd ou a toujou nesesè kòm rezèv.",
            "confirm": "Antre kòd biwo w la pou konfime",
        },
        "change": {
            "label": "Chanje kòd la",
            "current": "Kòd aktyèl la",
            "new": "Nouvo kòd",
            "confirm": "Konfime nouvo kòd la",
            "submit": "Chanje kòd la",
            "success": "Kòd la chanje. Biwo a bloke sou tout aparèy yo.",
        },
        "lockNow": "Bloke kounye a",
        "lockNowHint": "Bloke biwo a sou tout aparèy ou yo.",
    },
    "lock": {
        "checking": "N ap verifye sekirite biwo a…",
        "unavailable": {
            "title": "Sekirite biwo a pa disponib",
            "body": "Nou pa t ka jwenn sèvè a pou verifye kadna a. Eseye ankò.",
        },
        "setup": {
            "intro": {
                "title": "Ajoute yon dezyèm kadna",
                "body": "Biwo prive w la gen pwòp kòd pa li, apa de modpas kont ou an.",
                "start": "Konfigire kòd la",
                "notNow": "Pa kounye a",
            },
            "create": {
                "title": "Kreye kòd biwo a",
                "body": "Sèvi ak omwen {{digits}} chif. W ap bezwen li chak fwa ou ouvri biwo a.",
            },
            "confirm": {
                "title": "Konfime kòd la",
                "body": "Antre menm kòd la ankò.",
            },
            "continue": "Kontinye",
            "back": "Tounen",
            "mismatch": "Kòd yo pa menm. Eseye ankò.",
            "biometric": {
                "title": "Debloke ak {{method}}?",
                "body": "Ouvri biwo a pi vit. Kòd ou a rete kòm rezèv.",
                "enable": "Sèvi ak {{method}}",
                "skip": "Sote",
            },
        },
        "locked": {
            "title": "Biwo a bloke",
            "body": "Debloke biwo prive a pou wè kontni sa a.",
        },
        "placeholder": "Kòd biwo a",
        "unlock": "Debloke",
        "wrong": "Move kòd. Eseye ankò.",
        "cooldown": "Twòp tantativ. Eseye ankò nan {{seconds}} segonn.",
        "policy": "Kòd la dwe gen omwen {{digits}} chif.",
        "error": "Yon bagay pa mache. Eseye ankò.",
        "forgot": "Ou bliye kòd la?",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "Debloke biwo prive w la",
        "reset": {
            "title": "Reyinisyalize kòd biwo a",
            "body": "Konfime modpas kont ou an, epi chwazi yon nouvo kòd. Tout aparèy yo ap bloke.",
            "password": "Modpas kont lan",
            "newPasscode": "Nouvo kòd",
            "confirmPasscode": "Konfime nouvo kòd la",
            "submit": "Reyinisyalize kòd la",
            "cancel": "Anile",
            "failed": "Reyinisyalizasyon an echwe. Verifye modpas ou epi eseye ankò.",
        },
    },
}

PT = {
    "sections": {"security": "Segurança"},
    "security": {
        "title": "Segurança do escritório",
        "subtitle": "Controle como o seu escritório privado bloqueia e desbloqueia.",
        "row": {
            "label": "Segurança do escritório",
            "hint": "Código, biometria e tempo de bloqueio",
        },
        "relock": {
            "label": "Exigir código",
            "hint": "Quanto tempo até o escritório bloquear depois de sair do app.",
            "immediate": "Imediatamente",
            "1m": "Após 1 minuto",
            "5m": "Após 5 minutos",
            "15m": "Após 15 minutos",
        },
        "biometric": {
            "label": "Desbloquear com {{method}}",
            "hint": "Seu código continua sendo exigido como alternativa.",
            "confirm": "Digite o código do escritório para confirmar",
        },
        "change": {
            "label": "Alterar código",
            "current": "Código atual",
            "new": "Novo código",
            "confirm": "Confirmar novo código",
            "submit": "Alterar código",
            "success": "Código alterado. O escritório foi bloqueado em todos os dispositivos.",
        },
        "lockNow": "Bloquear agora",
        "lockNowHint": "Bloqueia o escritório em todos os seus dispositivos.",
    },
    "lock": {
        "checking": "Verificando a segurança do escritório…",
        "unavailable": {
            "title": "Segurança do escritório indisponível",
            "body": "Não foi possível contatar o servidor para verificar o bloqueio. Tente novamente.",
        },
        "setup": {
            "intro": {
                "title": "Adicione uma segunda tranca",
                "body": "Seu escritório privado tem um código próprio, separado da senha da sua conta.",
                "start": "Configurar código",
                "notNow": "Agora não",
            },
            "create": {
                "title": "Crie o código do escritório",
                "body": "Use pelo menos {{digits}} dígitos. Você precisará dele sempre que abrir o escritório.",
            },
            "confirm": {
                "title": "Confirme o código",
                "body": "Digite o mesmo código novamente.",
            },
            "continue": "Continuar",
            "back": "Voltar",
            "mismatch": "Os códigos não coincidem. Tente novamente.",
            "biometric": {
                "title": "Desbloquear com {{method}}?",
                "body": "Abra o escritório mais rápido. Seu código continua como alternativa.",
                "enable": "Usar {{method}}",
                "skip": "Pular",
            },
        },
        "locked": {
            "title": "Escritório bloqueado",
            "body": "Desbloqueie o escritório privado para ver este conteúdo.",
        },
        "placeholder": "Código do escritório",
        "unlock": "Desbloquear",
        "wrong": "Código incorreto. Tente novamente.",
        "cooldown": "Muitas tentativas. Tente novamente em {{seconds}} s.",
        "policy": "O código deve ter pelo menos {{digits}} dígitos.",
        "error": "Algo deu errado. Tente novamente.",
        "forgot": "Esqueceu o código?",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "Desbloqueie o seu escritório privado",
        "reset": {
            "title": "Redefinir o código do escritório",
            "body": "Confirme a senha da sua conta e escolha um novo código. Todos os dispositivos serão bloqueados.",
            "password": "Senha da conta",
            "newPasscode": "Novo código",
            "confirmPasscode": "Confirmar novo código",
            "submit": "Redefinir código",
            "cancel": "Cancelar",
            "failed": "Falha ao redefinir. Verifique sua senha e tente novamente.",
        },
    },
}

DE = {
    "sections": {"security": "Sicherheit"},
    "security": {
        "title": "Büro-Sicherheit",
        "subtitle": "Lege fest, wie dein privates Büro gesperrt und entsperrt wird.",
        "row": {
            "label": "Büro-Sicherheit",
            "hint": "Code, Biometrie und Sperrzeit",
        },
        "relock": {
            "label": "Code verlangen",
            "hint": "Wie schnell das Büro nach Verlassen der App gesperrt wird.",
            "immediate": "Sofort",
            "1m": "Nach 1 Minute",
            "5m": "Nach 5 Minuten",
            "15m": "Nach 15 Minuten",
        },
        "biometric": {
            "label": "Mit {{method}} entsperren",
            "hint": "Dein Code bleibt als Ersatz erforderlich.",
            "confirm": "Gib deinen Büro-Code zur Bestätigung ein",
        },
        "change": {
            "label": "Code ändern",
            "current": "Aktueller Code",
            "new": "Neuer Code",
            "confirm": "Neuen Code bestätigen",
            "submit": "Code ändern",
            "success": "Code geändert. Das Büro ist auf allen Geräten gesperrt.",
        },
        "lockNow": "Jetzt sperren",
        "lockNowHint": "Sperrt das Büro auf allen deinen Geräten.",
    },
    "lock": {
        "checking": "Büro-Sicherheit wird geprüft…",
        "unavailable": {
            "title": "Büro-Sicherheit nicht verfügbar",
            "body": "Der Server zur Prüfung der Sperre war nicht erreichbar. Versuche es erneut.",
        },
        "setup": {
            "intro": {
                "title": "Zweites Schloss hinzufügen",
                "body": "Dein privates Büro bekommt einen eigenen Code, getrennt von deinem Konto-Passwort.",
                "start": "Code einrichten",
                "notNow": "Nicht jetzt",
            },
            "create": {
                "title": "Büro-Code erstellen",
                "body": "Verwende mindestens {{digits}} Ziffern. Du brauchst ihn bei jedem Öffnen des Büros.",
            },
            "confirm": {
                "title": "Code bestätigen",
                "body": "Gib denselben Code noch einmal ein.",
            },
            "continue": "Weiter",
            "back": "Zurück",
            "mismatch": "Die Codes stimmen nicht überein. Versuche es erneut.",
            "biometric": {
                "title": "Mit {{method}} entsperren?",
                "body": "Öffne das Büro schneller. Dein Code bleibt der Ersatz.",
                "enable": "{{method}} verwenden",
                "skip": "Überspringen",
            },
        },
        "locked": {
            "title": "Büro gesperrt",
            "body": "Entsperre das private Büro, um diesen Inhalt zu sehen.",
        },
        "placeholder": "Büro-Code",
        "unlock": "Entsperren",
        "wrong": "Falscher Code. Versuche es erneut.",
        "cooldown": "Zu viele Versuche. Versuche es in {{seconds}} s erneut.",
        "policy": "Der Code muss mindestens {{digits}} Ziffern haben.",
        "error": "Etwas ist schiefgelaufen. Versuche es erneut.",
        "forgot": "Code vergessen?",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "Entsperre dein privates Büro",
        "reset": {
            "title": "Büro-Code zurücksetzen",
            "body": "Bestätige dein Konto-Passwort und wähle dann einen neuen Büro-Code. Alle Geräte werden gesperrt.",
            "password": "Konto-Passwort",
            "newPasscode": "Neuer Code",
            "confirmPasscode": "Neuen Code bestätigen",
            "submit": "Code zurücksetzen",
            "cancel": "Abbrechen",
            "failed": "Zurücksetzen fehlgeschlagen. Prüfe dein Passwort und versuche es erneut.",
        },
    },
}

AR = {
    "sections": {"security": "الأمان"},
    "security": {
        "title": "أمان المكتب",
        "subtitle": "تحكّم في كيفية قفل مكتبك الخاص وفتحه.",
        "row": {
            "label": "أمان المكتب",
            "hint": "رمز المرور والقياسات الحيوية وتوقيت القفل",
        },
        "relock": {
            "label": "طلب رمز المرور",
            "hint": "مدى سرعة قفل المكتب بعد مغادرة التطبيق.",
            "immediate": "فورًا",
            "1m": "بعد دقيقة واحدة",
            "5m": "بعد 5 دقائق",
            "15m": "بعد 15 دقيقة",
        },
        "biometric": {
            "label": "فتح القفل باستخدام {{method}}",
            "hint": "يبقى رمز المرور مطلوبًا كبديل.",
            "confirm": "أدخل رمز مرور المكتب للتأكيد",
        },
        "change": {
            "label": "تغيير رمز المرور",
            "current": "رمز المرور الحالي",
            "new": "رمز المرور الجديد",
            "confirm": "تأكيد رمز المرور الجديد",
            "submit": "تغيير رمز المرور",
            "success": "تم تغيير رمز المرور. أصبح المكتب مقفلًا على جميع الأجهزة.",
        },
        "lockNow": "القفل الآن",
        "lockNowHint": "يقفل المكتب على جميع أجهزتك.",
    },
    "lock": {
        "checking": "جارٍ التحقق من أمان المكتب…",
        "unavailable": {
            "title": "أمان المكتب غير متاح",
            "body": "تعذّر الوصول إلى الخادم للتحقق من قفل المكتب. حاول مرة أخرى.",
        },
        "setup": {
            "intro": {
                "title": "أضف قفلًا ثانيًا",
                "body": "يحصل مكتبك الخاص على رمز مرور خاص به، منفصل عن كلمة مرور حسابك.",
                "start": "إعداد رمز المرور",
                "notNow": "ليس الآن",
            },
            "create": {
                "title": "أنشئ رمز مرور المكتب",
                "body": "استخدم {{digits}} أرقام على الأقل. ستحتاجه في كل مرة تفتح فيها المكتب.",
            },
            "confirm": {
                "title": "أكّد رمز المرور",
                "body": "أدخل رمز المرور نفسه مرة أخرى.",
            },
            "continue": "متابعة",
            "back": "رجوع",
            "mismatch": "رمزا المرور غير متطابقين. حاول مرة أخرى.",
            "biometric": {
                "title": "فتح القفل باستخدام {{method}}؟",
                "body": "افتح المكتب بشكل أسرع. يبقى رمز المرور بديلًا.",
                "enable": "استخدام {{method}}",
                "skip": "تخطٍ",
            },
        },
        "locked": {
            "title": "المكتب مقفل",
            "body": "افتح قفل المكتب الخاص لعرض هذا المحتوى.",
        },
        "placeholder": "رمز مرور المكتب",
        "unlock": "فتح القفل",
        "wrong": "رمز مرور خاطئ. حاول مرة أخرى.",
        "cooldown": "محاولات كثيرة جدًا. حاول مرة أخرى بعد {{seconds}} ثانية.",
        "policy": "يجب أن يتكوّن رمز المرور من {{digits}} أرقام على الأقل.",
        "error": "حدث خطأ ما. حاول مرة أخرى.",
        "forgot": "هل نسيت رمز المرور؟",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "افتح قفل مكتبك الخاص",
        "reset": {
            "title": "إعادة تعيين رمز مرور المكتب",
            "body": "أكّد كلمة مرور حسابك ثم اختر رمز مرور جديدًا للمكتب. سيتم قفل جميع الأجهزة.",
            "password": "كلمة مرور الحساب",
            "newPasscode": "رمز المرور الجديد",
            "confirmPasscode": "تأكيد رمز المرور الجديد",
            "submit": "إعادة تعيين رمز المرور",
            "cancel": "إلغاء",
            "failed": "فشلت إعادة التعيين. تحقّق من كلمة المرور وحاول مرة أخرى.",
        },
    },
}

HI = {
    "sections": {"security": "सुरक्षा"},
    "security": {
        "title": "कार्यालय सुरक्षा",
        "subtitle": "नियंत्रित करें कि आपका निजी कार्यालय कैसे लॉक और अनलॉक होता है।",
        "row": {
            "label": "कार्यालय सुरक्षा",
            "hint": "पासकोड, बायोमेट्रिक्स और लॉक समय",
        },
        "relock": {
            "label": "पासकोड आवश्यक",
            "hint": "ऐप छोड़ने के बाद कार्यालय कितनी जल्दी लॉक होता है।",
            "immediate": "तुरंत",
            "1m": "1 मिनट बाद",
            "5m": "5 मिनट बाद",
            "15m": "15 मिनट बाद",
        },
        "biometric": {
            "label": "{{method}} से अनलॉक करें",
            "hint": "आपका पासकोड बैकअप के रूप में आवश्यक रहेगा।",
            "confirm": "पुष्टि के लिए अपना कार्यालय पासकोड दर्ज करें",
        },
        "change": {
            "label": "पासकोड बदलें",
            "current": "वर्तमान पासकोड",
            "new": "नया पासकोड",
            "confirm": "नए पासकोड की पुष्टि करें",
            "submit": "पासकोड बदलें",
            "success": "पासकोड बदल गया। कार्यालय सभी डिवाइस पर लॉक हो गया है।",
        },
        "lockNow": "अभी लॉक करें",
        "lockNowHint": "आपके सभी डिवाइस पर कार्यालय लॉक करता है।",
    },
    "lock": {
        "checking": "कार्यालय सुरक्षा जाँची जा रही है…",
        "unavailable": {
            "title": "कार्यालय सुरक्षा अनुपलब्ध",
            "body": "लॉक जाँचने के लिए सर्वर से संपर्क नहीं हो सका। फिर से कोशिश करें।",
        },
        "setup": {
            "intro": {
                "title": "दूसरा ताला जोड़ें",
                "body": "आपके निजी कार्यालय का अपना पासकोड होता है, जो आपके खाते के पासवर्ड से अलग है।",
                "start": "पासकोड सेट करें",
                "notNow": "अभी नहीं",
            },
            "create": {
                "title": "कार्यालय पासकोड बनाएँ",
                "body": "कम से कम {{digits}} अंक उपयोग करें। कार्यालय खोलने पर हर बार इसकी आवश्यकता होगी।",
            },
            "confirm": {
                "title": "पासकोड की पुष्टि करें",
                "body": "वही पासकोड फिर से दर्ज करें।",
            },
            "continue": "जारी रखें",
            "back": "वापस",
            "mismatch": "पासकोड मेल नहीं खाते। फिर से कोशिश करें।",
            "biometric": {
                "title": "{{method}} से अनलॉक करें?",
                "body": "कार्यालय तेज़ी से खोलें। आपका पासकोड बैकअप बना रहेगा।",
                "enable": "{{method}} उपयोग करें",
                "skip": "छोड़ें",
            },
        },
        "locked": {
            "title": "कार्यालय लॉक है",
            "body": "यह सामग्री देखने के लिए निजी कार्यालय अनलॉक करें।",
        },
        "placeholder": "कार्यालय पासकोड",
        "unlock": "अनलॉक करें",
        "wrong": "गलत पासकोड। फिर से कोशिश करें।",
        "cooldown": "बहुत अधिक प्रयास। {{seconds}} सेकंड में फिर से कोशिश करें।",
        "policy": "पासकोड में कम से कम {{digits}} अंक होने चाहिए।",
        "error": "कुछ गलत हुआ। फिर से कोशिश करें।",
        "forgot": "पासकोड भूल गए?",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "अपना निजी कार्यालय अनलॉक करें",
        "reset": {
            "title": "कार्यालय पासकोड रीसेट करें",
            "body": "अपने खाते के पासवर्ड की पुष्टि करें, फिर नया कार्यालय पासकोड चुनें। सभी डिवाइस लॉक हो जाएँगे।",
            "password": "खाते का पासवर्ड",
            "newPasscode": "नया पासकोड",
            "confirmPasscode": "नए पासकोड की पुष्टि करें",
            "submit": "पासकोड रीसेट करें",
            "cancel": "रद्द करें",
            "failed": "रीसेट विफल रहा। अपना पासवर्ड जाँचें और फिर से कोशिश करें।",
        },
    },
}

JA = {
    "sections": {"security": "セキュリティ"},
    "security": {
        "title": "オフィスのセキュリティ",
        "subtitle": "プライベートオフィスのロックと解除の方法を設定します。",
        "row": {
            "label": "オフィスのセキュリティ",
            "hint": "パスコード、生体認証、再ロックのタイミング",
        },
        "relock": {
            "label": "パスコードを要求",
            "hint": "アプリを離れてからオフィスがロックされるまでの時間。",
            "immediate": "すぐに",
            "1m": "1分後",
            "5m": "5分後",
            "15m": "15分後",
        },
        "biometric": {
            "label": "{{method}}でロック解除",
            "hint": "パスコードは引き続き予備として必要です。",
            "confirm": "確認のためオフィスのパスコードを入力してください",
        },
        "change": {
            "label": "パスコードを変更",
            "current": "現在のパスコード",
            "new": "新しいパスコード",
            "confirm": "新しいパスコードを確認",
            "submit": "パスコードを変更",
            "success": "パスコードを変更しました。すべての端末でオフィスがロックされました。",
        },
        "lockNow": "今すぐロック",
        "lockNowHint": "すべての端末でオフィスをロックします。",
    },
    "lock": {
        "checking": "オフィスのセキュリティを確認しています…",
        "unavailable": {
            "title": "オフィスのセキュリティを利用できません",
            "body": "ロックの確認のためサーバーに接続できませんでした。もう一度お試しください。",
        },
        "setup": {
            "intro": {
                "title": "2つ目のロックを追加",
                "body": "プライベートオフィスには、アカウントのパスワードとは別の専用パスコードを設定します。",
                "start": "パスコードを設定",
                "notNow": "今はしない",
            },
            "create": {
                "title": "オフィスのパスコードを作成",
                "body": "{{digits}}桁以上を使用してください。オフィスを開くたびに必要になります。",
            },
            "confirm": {
                "title": "パスコードを確認",
                "body": "同じパスコードをもう一度入力してください。",
            },
            "continue": "続ける",
            "back": "戻る",
            "mismatch": "パスコードが一致しません。もう一度お試しください。",
            "biometric": {
                "title": "{{method}}でロック解除しますか？",
                "body": "オフィスをより速く開けます。パスコードは予備として残ります。",
                "enable": "{{method}}を使う",
                "skip": "スキップ",
            },
        },
        "locked": {
            "title": "オフィスはロック中",
            "body": "このコンテンツを見るにはプライベートオフィスのロックを解除してください。",
        },
        "placeholder": "オフィスのパスコード",
        "unlock": "ロック解除",
        "wrong": "パスコードが違います。もう一度お試しください。",
        "cooldown": "試行回数が多すぎます。{{seconds}}秒後にもう一度お試しください。",
        "policy": "パスコードは{{digits}}桁以上にしてください。",
        "error": "問題が発生しました。もう一度お試しください。",
        "forgot": "パスコードをお忘れですか？",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "プライベートオフィスのロックを解除",
        "reset": {
            "title": "オフィスのパスコードをリセット",
            "body": "アカウントのパスワードを確認してから、新しいオフィスのパスコードを選んでください。すべての端末がロックされます。",
            "password": "アカウントのパスワード",
            "newPasscode": "新しいパスコード",
            "confirmPasscode": "新しいパスコードを確認",
            "submit": "パスコードをリセット",
            "cancel": "キャンセル",
            "failed": "リセットに失敗しました。パスワードを確認してもう一度お試しください。",
        },
    },
}

KO = {
    "sections": {"security": "보안"},
    "security": {
        "title": "오피스 보안",
        "subtitle": "프라이빗 오피스의 잠금 및 잠금 해제 방식을 설정합니다.",
        "row": {
            "label": "오피스 보안",
            "hint": "암호, 생체 인식, 재잠금 시간",
        },
        "relock": {
            "label": "암호 요구",
            "hint": "앱을 떠난 후 오피스가 잠기기까지의 시간입니다.",
            "immediate": "즉시",
            "1m": "1분 후",
            "5m": "5분 후",
            "15m": "15분 후",
        },
        "biometric": {
            "label": "{{method}}(으)로 잠금 해제",
            "hint": "암호는 대체 수단으로 계속 필요합니다.",
            "confirm": "확인을 위해 오피스 암호를 입력하세요",
        },
        "change": {
            "label": "암호 변경",
            "current": "현재 암호",
            "new": "새 암호",
            "confirm": "새 암호 확인",
            "submit": "암호 변경",
            "success": "암호가 변경되었습니다. 모든 기기에서 오피스가 잠겼습니다.",
        },
        "lockNow": "지금 잠그기",
        "lockNowHint": "모든 기기에서 오피스를 잠급니다.",
    },
    "lock": {
        "checking": "오피스 보안을 확인하는 중…",
        "unavailable": {
            "title": "오피스 보안을 사용할 수 없음",
            "body": "잠금 확인을 위해 서버에 연결할 수 없습니다. 다시 시도하세요.",
        },
        "setup": {
            "intro": {
                "title": "두 번째 잠금 추가",
                "body": "프라이빗 오피스에는 계정 비밀번호와 별도의 전용 암호가 설정됩니다.",
                "start": "암호 설정",
                "notNow": "나중에",
            },
            "create": {
                "title": "오피스 암호 만들기",
                "body": "{{digits}}자리 이상 사용하세요. 오피스를 열 때마다 필요합니다.",
            },
            "confirm": {
                "title": "암호 확인",
                "body": "같은 암호를 다시 입력하세요.",
            },
            "continue": "계속",
            "back": "뒤로",
            "mismatch": "암호가 일치하지 않습니다. 다시 시도하세요.",
            "biometric": {
                "title": "{{method}}(으)로 잠금 해제할까요?",
                "body": "오피스를 더 빠르게 열 수 있습니다. 암호는 대체 수단으로 유지됩니다.",
                "enable": "{{method}} 사용",
                "skip": "건너뛰기",
            },
        },
        "locked": {
            "title": "오피스 잠김",
            "body": "이 콘텐츠를 보려면 프라이빗 오피스의 잠금을 해제하세요.",
        },
        "placeholder": "오피스 암호",
        "unlock": "잠금 해제",
        "wrong": "잘못된 암호입니다. 다시 시도하세요.",
        "cooldown": "시도 횟수가 너무 많습니다. {{seconds}}초 후에 다시 시도하세요.",
        "policy": "암호는 {{digits}}자리 이상이어야 합니다.",
        "error": "문제가 발생했습니다. 다시 시도하세요.",
        "forgot": "암호를 잊으셨나요?",
        "biometricFaceId": "Face ID",
        "biometricTouchId": "Touch ID",
        "biometricPrompt": "프라이빗 오피스 잠금 해제",
        "reset": {
            "title": "오피스 암호 재설정",
            "body": "계정 비밀번호를 확인한 다음 새 오피스 암호를 선택하세요. 모든 기기가 잠깁니다.",
            "password": "계정 비밀번호",
            "newPasscode": "새 암호",
            "confirmPasscode": "새 암호 확인",
            "submit": "암호 재설정",
            "cancel": "취소",
            "failed": "재설정에 실패했습니다. 비밀번호를 확인하고 다시 시도하세요.",
        },
    },
}

ZH = {
    "sections": {"security": "安全"},
    "security": {
        "title": "办公室安全",
        "subtitle": "控制您的私人办公室如何锁定和解锁。",
        "row": {
            "label": "办公室安全",
            "hint": "密码、生物识别和重新锁定时间",
        },
        "relock": {
            "label": "要求输入密码",
            "hint": "离开应用后办公室多久锁定。",
            "immediate": "立即",
            "1m": "1 分钟后",
            "5m": "5 分钟后",
            "15m": "15 分钟后",
        },
        "biometric": {
            "label": "使用{{method}}解锁",
            "hint": "密码仍然是必需的后备方式。",
            "confirm": "输入办公室密码以确认",
        },
        "change": {
            "label": "更改密码",
            "current": "当前密码",
            "new": "新密码",
            "confirm": "确认新密码",
            "submit": "更改密码",
            "success": "密码已更改。办公室已在所有设备上锁定。",
        },
        "lockNow": "立即锁定",
        "lockNowHint": "在您的所有设备上锁定办公室。",
    },
    "lock": {
        "checking": "正在检查办公室安全…",
        "unavailable": {
            "title": "办公室安全不可用",
            "body": "无法连接服务器检查办公室锁定状态。请重试。",
        },
        "setup": {
            "intro": {
                "title": "添加第二道锁",
                "body": "您的私人办公室拥有独立密码，与账户密码分开。",
                "start": "设置密码",
                "notNow": "暂不",
            },
            "create": {
                "title": "创建办公室密码",
                "body": "至少使用 {{digits}} 位数字。每次打开办公室时都需要输入。",
            },
            "confirm": {
                "title": "确认密码",
                "body": "再次输入相同的密码。",
            },
            "continue": "继续",
            "back": "返回",
            "mismatch": "两次密码不一致。请重试。",
            "biometric": {
                "title": "使用{{method}}解锁？",
                "body": "更快打开办公室。密码仍作为后备方式。",
                "enable": "使用{{method}}",
                "skip": "跳过",
            },
        },
        "locked": {
            "title": "办公室已锁定",
            "body": "解锁私人办公室以查看此内容。",
        },
        "placeholder": "办公室密码",
        "unlock": "解锁",
        "wrong": "密码错误。请重试。",
        "cooldown": "尝试次数过多。请在 {{seconds}} 秒后重试。",
        "policy": "密码至少需要 {{digits}} 位数字。",
        "error": "出了点问题。请重试。",
        "forgot": "忘记密码？",
        "biometricFaceId": "面容 ID",
        "biometricTouchId": "触控 ID",
        "biometricPrompt": "解锁您的私人办公室",
        "reset": {
            "title": "重置办公室密码",
            "body": "确认您的账户密码，然后选择新的办公室密码。所有设备都将被锁定。",
            "password": "账户密码",
            "newPasscode": "新密码",
            "confirmPasscode": "确认新密码",
            "submit": "重置密码",
            "cancel": "取消",
            "failed": "重置失败。请检查密码后重试。",
        },
    },
}

EXTENDED = {
    "en": EN, "es": ES, "fr": FR, "ht": HT, "pt": PT, "de": DE,
    "ar": AR, "hi": HI, "ja": JA, "ko": KO, "zh": ZH,
}


def merge(target: dict, source: dict) -> int:
    """Additive deep merge. Returns the number of leaves written."""
    written = 0
    for key, value in source.items():
        if isinstance(value, dict):
            node = target.setdefault(key, {})
            if not isinstance(node, dict):
                raise SystemExit(f"refusing to overwrite non-object at {key!r}")
            written += merge(node, value)
        elif key not in target:
            target[key] = value
            written += 1
    return written


def write(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    for locale in sorted(EXTENDED):
        core_path = os.path.join(ROOT, locale, "core.json")
        with open(core_path, encoding="utf-8") as handle:
            core = json.load(handle)
        n_core = merge(core.setdefault("common", {}).setdefault("screens", {}), CORE[locale])
        write(core_path, core)

        ext_path = os.path.join(ROOT, locale, "extended.json")
        with open(ext_path, encoding="utf-8") as handle:
            ext = json.load(handle)
        n_ext = merge(ext.setdefault("premium", {}).setdefault("privateOffice", {}), EXTENDED[locale])
        write(ext_path, ext)

        print(f"{locale}: core +{n_core}, extended +{n_ext}")


if __name__ == "__main__":
    main()
