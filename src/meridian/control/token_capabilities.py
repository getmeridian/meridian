"""Catalog-derived grants and their shared English, Russian, Persian, and Chinese labels."""

from functools import cache

from meridian.control.route_types import ApiRoute
from meridian.core.languages import LANGUAGES
from meridian.core.token_models import (
    TOKEN_SCOPES,
    CapabilityCatalog,
    CapabilityOperation,
    CapabilityPermission,
    CapabilityRecord,
    TokenCapability,
    TokenCreate,
    TokenScope,
    scope_covers,
)

# Human wording belongs to the adapters; API, CLI, and console consume these same labels.
_LABELS: dict[TokenCapability, tuple[str, str, str, str]] = {
    "metrics.read": (
        "Read Prometheus node capacity and service metrics",
        "Смотреть показатели серверов и сервиса в Prometheus",
        "خواندن آمار منابع گره‌ها و سرویس در Prometheus",
        "读取 Prometheus 节点资源和服务指标",
    ),
    "people.delivery": (
        "Choose people's client routing policies",
        "Выбирать политики маршрутизации для пользователей",
        "انتخاب سیاست مسیریابی دستگاه افراد",
        "选择用户的客户端路由策略",
    ),
    "delivery.read": (
        "Read reviewed client routing policies",
        "Читать проверенные политики маршрутизации",
        "خواندن سیاست‌های مسیریابی بازبینی‌شده",
        "读取已审阅的客户端路由策略",
    ),
    "api.read": (
        "Read the API contract and permission catalog",
        "Читать описание API и список прав",
        "خواندن قرارداد API و فهرست دسترسی‌ها",
        "读取 API 契约和权限目录",
    ),
    "fleet.read": (
        "Read fleet health, usage, and settings",
        "Смотреть состояние, трафик и настройки серверов",
        "مشاهده وضعیت، مصرف و تنظیمات سرورها",
        "查看服务器状态、用量和设置",
    ),
    "topology.read": (
        "Read node configurations and endpoint credentials",
        "Читать конфигурации узлов и данные подключения",
        "خواندن پیکربندی گره‌ها و اعتبارنامه‌های اتصال",
        "读取节点配置和连接凭证",
    ),
    "fleet.apply": (
        "Apply topology and record deployment results",
        "Применять топологию и записывать результаты развёртывания",
        "اعمال توپولوژی و ثبت نتیجه استقرار",
        "应用拓扑并记录部署结果",
    ),
    "nodes.enroll": (
        "Issue node enrollment credentials",
        "Выдавать данные для регистрации узлов",
        "صدور اعتبارنامه ثبت گره‌ها",
        "签发节点注册凭证",
    ),
    "people.read": (
        "Read people’s account state and allowed device records",
        "Смотреть состояние аккаунтов и разрешённые сведения об устройствах",
        "مشاهده وضعیت حساب‌ها و اطلاعات مجاز دستگاه‌ها",
        "查看用户账户状态及隐私设置允许的设备记录",
    ),
    "people.query": (
        "Query people with shared filters and account facets",
        "Искать пользователей по общим фильтрам и параметрам аккаунтов",
        "جست‌وجوی افراد با فیلترهای مشترک و ویژگی‌های حساب",
        "按共享筛选条件及账户属性查询用户",
    ),
    "people.batch": (
        "Review and apply bulk account changes, including deletion",
        "Проверять и массово менять аккаунты, включая удаление",
        "بازبینی و اعمال تغییرات گروهی حساب‌ها، از جمله حذف",
        "审阅并应用批量账户变更，包括删除",
    ),
    "people.private": (
        "Read private notes, tags, and access membership",
        "Читать личные заметки, теги и назначения профилей доступа",
        "خواندن یادداشت‌ها، برچسب‌ها و عضویت دسترسی خصوصی",
        "读取私人备注、标签和访问配置成员关系",
    ),
    "people.edit": (
        "Create people, edit account settings, and extend access",
        "Добавлять людей, менять настройки аккаунтов и продлевать доступ",
        "افزودن افراد، ویرایش تنظیمات حساب و تمدید دسترسی",
        "创建用户、修改账户设置并延长访问期限",
    ),
    "people.credentials": (
        "Read connection keys, subscription links, and QR data",
        "Читать ключи подключения, ссылки подписки и данные QR-кодов",
        "خواندن کلیدهای اتصال، پیوند اشتراک و داده QR",
        "读取连接密钥、订阅链接和二维码数据",
    ),
    "people.reset": (
        "Reset people’s current-period traffic",
        "Сбрасывать трафик пользователей за текущий период",
        "بازنشانی مصرف دوره جاری افراد",
        "重置用户当前周期的流量",
    ),
    "people.reissue": (
        "Replace people’s connection credentials",
        "Перевыпускать данные подключения пользователей",
        "جایگزینی اعتبارنامه اتصال افراد",
        "重新签发用户连接凭证",
    ),
    "people.remove": ("Remove people’s accounts", "Удалять аккаунты пользователей", "حذف حساب افراد", "删除用户账户"),
    "people.devices": (
        "Remove device registrations",
        "Удалять зарегистрированные устройства",
        "حذف ثبت دستگاه‌ها",
        "移除设备注册记录",
    ),
    "people.bulk": (
        "Import people and connection credentials",
        "Импортировать пользователей и данные подключения",
        "درون‌ریزی افراد و اعتبارنامه‌های اتصال",
        "导入用户和连接凭证",
    ),
    "commands.read": (
        "Read command results and affected inbound lists",
        "Смотреть результаты команд и затронутые входящие подключения",
        "مشاهده نتیجه فرمان‌ها و ورودی‌های تحت تأثیر",
        "查看命令结果及受影响入站列表",
    ),
    "commands.restart": (
        "Restart Xray units",
        "Перезапускать службы Xray",
        "راه‌اندازی مجدد سرویس Xray",
        "重启 Xray 服务",
    ),
    "commands.run": (
        "Queue node reconcile and traffic-reset commands",
        "Отправлять узлам команды сверки состояния и сброса трафика",
        "ارسال فرمان همگام‌سازی و بازنشانی ترافیک به گره‌ها",
        "向节点发送状态协调和流量重置命令",
    ),
    "commands.cut_off": (
        "Cut off active sessions when removing or reissuing credentials",
        "Завершать активные сеансы при удалении или перевыпуске данных подключения",
        "قطع نشست‌های فعال هنگام حذف یا صدور اعتبارنامه تازه",
        "删除或重新签发凭证时切断活动会话",
    ),
    "backup.read": (
        "Download encrypted snapshots and read public backup keys",
        "Скачивать зашифрованные снимки и читать открытые ключи резервных копий",
        "دریافت snapshot رمزنگاری‌شده و خواندن کلیدهای عمومی پشتیبان",
        "下载加密快照并读取备份公钥",
    ),
    "backup.edit": (
        "Manage backup keys and stored fleet files",
        "Управлять ключами резервных копий и сохранёнными файлами серверов",
        "مدیریت کلیدهای پشتیبان و فایل‌های ذخیره‌شده سرورها",
        "管理备份密钥和保存的服务器文件",
    ),
    "settings.edit": (
        "Change privacy, notices, and page language",
        "Менять настройки конфиденциальности, сообщения и язык страниц",
        "تغییر حریم خصوصی، اعلان‌ها و زبان صفحه",
        "修改隐私、通知和页面语言",
    ),
    "tokens.read": (
        "Read token names, permissions, and expiry",
        "Смотреть названия, права и сроки действия токенов",
        "مشاهده نام، دسترسی و انقضای توکن‌ها",
        "查看令牌名称、权限和到期时间",
    ),
    "tokens.issue": (
        "Issue tokens within the permissions and expiry held",
        "Выдавать токены в пределах своих прав и срока действия",
        "صدور توکن در محدوده دسترسی و اعتبار فعلی",
        "在自身权限和有效期内签发令牌",
    ),
    "tokens.revoke": ("Revoke API tokens", "Отзывать API-токены", "لغو توکن‌های API", "撤销 API 令牌"),
    "revisions.read": (
        "Read reviewed fleet revisions",
        "Читать проверенные ревизии конфигурации серверов",
        "خواندن بازبینی‌های تأییدشده سرورها",
        "读取已审阅的服务器配置版本",
    ),
    "revisions.propose": (
        "Propose fleet revisions",
        "Предлагать изменения конфигурации серверов",
        "پیشنهاد تغییر پیکربندی سرورها",
        "提出服务器配置变更",
    ),
    "revisions.approve": (
        "Approve other callers’ proposals",
        "Одобрять изменения, предложенные другими токенами",
        "تأیید پیشنهادهای توکن‌های دیگر",
        "批准其他令牌提出的变更",
    ),
    "revisions.approve_own": (
        "Approve own proposals with revisions.approve",
        "Одобрять свои изменения вместе с правом revisions.approve",
        "تأیید پیشنهاد خود همراه با revisions.approve",
        "配合 revisions.approve 批准自己提出的变更",
    ),
    "owner.read": (
        "Read console owner setup state",
        "Смотреть состояние настройки владельца веб-панели",
        "مشاهده وضعیت راه‌اندازی مالک پنل",
        "查看面板所有者的设置状态",
    ),
    "console.read": (
        "Use console-specific fleet and sharing views",
        "Открывать представления серверов и ссылок для веб-панели",
        "استفاده از نمای سرورها و اشتراک‌گذاری در پنل",
        "使用面板专用的服务器和分享视图",
    ),
}


def permission(capability: TokenCapability) -> CapabilityPermission:
    return CapabilityPermission(capability=capability, labels=dict(zip(LANGUAGES, _LABELS[capability], strict=True)))


def catalog_routes() -> tuple[ApiRoute, ...]:
    # Imported after service construction to keep model/schema imports independent of HTTP startup.
    from meridian.control.route_catalog import API_ROUTES, PROTECTED_UI_ROUTES

    return (*API_ROUTES, *PROTECTED_UI_ROUTES)


@cache
def legacy_capabilities(scope: TokenScope) -> frozenset[TokenCapability]:
    """Freeze exactly the routes and response fields a compatibility scope could already reach."""
    grants: set[TokenCapability] = set()
    for route in catalog_routes():
        if "root" in route.capabilities or not any(scope_covers(scope, needed) for needed in route.scopes):
            continue
        for capability in route.capabilities:
            if capability != "root":
                grants.add(capability)
        for condition in route.conditional_capabilities:
            scopes = (condition.scope,) if isinstance(condition.scope, str) else condition.scope
            if any(scope_covers(scope, needed) for needed in scopes):
                grants.add(condition.capability)
    return frozenset(grants)


def requested_capabilities(request: TokenCreate) -> frozenset[TokenCapability]:
    return legacy_capabilities(request.scope) if request.scope is not None else frozenset(request.capabilities or ())


@cache
def console_capabilities() -> frozenset[TokenCapability]:
    """Keep existing console presets and permit delegation of its own service-owned edits/restarts."""
    return frozenset().union(
        *(legacy_capabilities(scope) for scope in TOKEN_SCOPES if scope != "admin"),
        {"settings.edit", "commands.restart", "metrics.read"},
    )


@cache
def capability_catalog() -> CapabilityCatalog:
    operations: dict[TokenCapability, list[CapabilityOperation]] = {}
    for route in catalog_routes():
        for capability in route.capabilities:
            if capability != "root":
                operations.setdefault(capability, []).append(CapabilityOperation(method=route.method, path=route.path))
        for condition in route.conditional_capabilities:
            operations.setdefault(condition.capability, []).append(
                CapabilityOperation(method=route.method, path=route.path, conditional=True)
            )
    return CapabilityCatalog(
        capabilities=[
            CapabilityRecord(
                **permission(capability).to_data(),
                operations=rows,
                console_delegable=capability in console_capabilities(),
            )
            for capability, rows in sorted(operations.items())
        ],
        presets={scope: sorted(legacy_capabilities(scope)) for scope in TOKEN_SCOPES},
    )
