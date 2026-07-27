
## M 2.5
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=['sber_pay_online', 'sber_pay_face_scan']; terminal=[]
MAIN_IDEA: Условия возможности совершения Операций оплаты с использованием SberPay
TEXT: Возможность совершения Операций оплаты с использованием SberPay предоставляется в следующем порядке и на следующих условиях:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 2.5.1
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=['sber_pay_online']; terminal=[]
MAIN_IDEA: Для SberPayOnline
TEXT: SberPayOnline:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 2.5.1.1
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=['sber_pay_online']; terminal=[]
MAIN_IDEA: Важен факт, что бесконтактная оплата подключается автоматически
TEXT: при подключении Интернет-эквайринга SberPayOnline подключается автоматически с применением единого тарифа по Интернет-эквайрингу;
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring]; payment_method=[sber_pay_online], профиль=[нет выбранного значения].

## M 2.5.1.2
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=['sber_pay_online']; terminal=[]
MAIN_IDEA: 
TEXT: без подключения Интернет-эквайринга SberPayOnline подключается как отдельная услуга с отдельной тарификацией.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[sber_pay_online], профиль=[нет выбранного значения].

## M 2.5.2
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['sber_pay_face_scan']; terminal=[]
MAIN_IDEA: 
TEXT: SberPayFaceScan:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 2.5.2.1
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['sber_pay_face_scan']; terminal=[]
MAIN_IDEA: Важен факт, что возможность принятия оплаты по биометрии оформляется отдельным заявлением
TEXT: Подключается дополнительно к Торговому эквайрингу на основании оформленной на бумажном носителе и подписанной уполномоченным представителем Предприятия Информации о ТСТ.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[sber_pay_face_scan], профиль=[нет выбранного значения].

## M 2.6
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: 
TEXT: Возможность совершения Операций оплаты по QR-коду на Смарт-терминалах, Электронных терминалах, ККТ Предприятия предоставляется в следующем порядке и на следующих условиях:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 2.6.1
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['smart']
MAIN_IDEA: 
TEXT: Для Смарт-терминалов:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 2.6.1.1
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=['smart']
MAIN_IDEA: Для приема QR на смарт-терминал не нужно отдельное ПО
TEXT: QR-код подключается при установке Смарт-терминала Банка/при установке программного обеспечения Банка для Смарт-терминала Предприятия в ТСТ.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 2.6.2
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: 
TEXT: Для Электронных терминалов, работающих под управлением ККТ Предприятия, возможны следующие виды подключения динамического QR-кода в зависимости от программного обеспечения ККТ:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 2.6.2.1
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Использование QR-API на собственном программном обеспечении Предприятия и размещение условий QR-API на сайте Банка
TEXT: при наличии у Предприятия собственного программного обеспечения Банк предоставляет возможность использования QR-кода на ККТ посредством передачи Банком Предприятию информации через API (далее − QR-API). Порядок, условия и ответственность сторон при подключении QR-API размещены на Официальном сайте Банка;
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 2.6.2.2
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Самостоятельная доработка Предприятием собственного программного обеспечения для QR и порядок подключения через API или Вендора
TEXT: при отсутствии у Предприятия собственного программного обеспечения с возможностью подключения QR-кода Предприятие самостоятельно дорабатывает его с привлечением Вендора . Проведение первой Операции оплаты с использованием QR-кода в ТСТ обозначает активацию Предприятием услуги QR-кода.Банк может передавать Вендору параметры формирования QR-кода (далее – QR-Вендор) для дальнейшей передачи его Предприятию, после доработки программного обеспечения для ККТ, через API на основании отдельно заключенного договора с Вендором. Подключение QR-кода (в том числе с подключением через QR-API и/или QR-Вендор) осуществляется на основании Приложения №1 и Приложения №1.1 к настоящему Договору.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 2.6.3
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: 
TEXT: Для Электронных терминалов при подключении на них QR-кода Вендором, которым является Банк:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 2.6.3.1
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Автоматическое подключение QR при установке терминалов, предоставленных Банком
TEXT: QR-код подключается при установке Электронного терминала в ТСТ;
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 2.6.3.2
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Отключение QR на терминалах Банка по обращению Предприятия в Банк
TEXT: отключение QR-кода по инициативе Предприятия осуществляется при обращении в Банк одним из способов, указанных в п.2.3.1. – п.2.3.4., п.2.3.7. Договора.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 3
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: «ПРЕДМЕТ ДОГОВОРА»
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 4
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: «ПРАВА И ОБЯЗАННОСТИ ПРЕДПРИЯТИЯ»
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 4.1
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Предприятие имеет право:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 4.1.3
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Важен сам факт, что QR выдаёт Банк
TEXT: Использовать для приема оплаты Товаров/услуг по SberPayQR/Плати QR один или несколько QR-кодов, полученных в Банке и размещенных на Смарт-терминалах, Электронных терминалах, Мобильных устройствах Предприятия, а также на ККТ, принадлежащей Предприятию, или на видном месте, в том числе на бумажном носителе.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 4.2
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Предприятие обязуется:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 4.2.2
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: Обязанность Предприятия соблюдать договор и выполнять требования информационных и инструктивных материалов Банка
TEXT: Соблюдать положения Договора, а также выполнять требования, содержащиеся в информационных/ инструктивных материалах, предоставляемых Банком.
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 4.2.3
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=['fz_223']; payment=[]; terminal=[]
MAIN_IDEA: В обязанности предприятия входит ознакомление с изменениями в документах.
TEXT: Самостоятельно и своевременно знакомиться с изменениями, внесенными в документы, ссылки на которые даются в настоящем Договоре, размещенными на Официальном сайте Банка. Несвоевременное ознакомление Предприятия с изменениями, внесенными в вышеуказанные документы, не является основанием для их неприменения Банком.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_lot=[fz_223], профиль=[fz_44].

## M 4.2.8
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: Важно, что цена не зависит от способа оплаты
TEXT: Предоставлять Покупателям полный набор Товаров/услуг по ценам, не превышающим цены Предприятия при расчетах за наличные денежные средства.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 4.2.16.2
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: Важно, что данный пункт относится ко всем сотрудникам кроме руководителя, важно наличие согласий, обязанность подтвердить наличие согласий, важен состав ПДн
TEXT: Предприятие гарантирует наличие согласий на обработку Банком персональных данных своих работников, состав которых включает: ФИО, мобильный телефон, электронная почта, должность и место работы, а также на их дальнейшую передачу в Обслуживающие компании, действующие от лица Банка, необходимых для целей заключения и исполнения настоящего Договора. Предприятие обязуется предоставить подтверждение наличия согласий по письменному запросу Банка в соответствии с пунктом 4.2.16.3 Договора.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 4.2.17
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: Важен сам факт обязанности соблюдения стандарта, а так же факт обязанности предоставления подтверждения
TEXT: Обеспечить соблюдение требований Стандарта PCI DSS, размещенного на сайте в сети интернет: https://www.pcisecuritystandards.org и предоставлять по запросу Банка результаты проведения оценки соответствия в виде аттестата соответствия или листа самооценки на электронный адрес: pcidss@sberbank.ru.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.21
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: При осуществлении Операций на Ресурсе (Интернет-эквайринг):
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 4.2.21.1
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Приступить к проведению Операций на Ресурсе только после успешного завершения проверки выбранных Предприятием/ТСТ Операций на тестовой среде.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.21.2
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Соответствовать Требованиям Банка к Ресурсу Предприятия, размещенным на Официальном сайте Банка.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.21.3
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Подтверждать авторизованные с использованием Предавторизации суммы к списанию через СПЭП не позднее 5 (пяти) календарных дней с даты совершения Операции на сумму фактически оказанных Товаров/услуг, в соответствии с Порядком проведения операций в Торгово-сервисных точках/на Ресурсе.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.21.4
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Согласовывать с Банком дизайн Платежной страницы, включая электронные варианты информационных наклеек.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.21.5
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Провести мероприятия по интеграции Ресурса со СПЭП и соответствующие интеграционные тесты в течение 30 (тридцати) календарных дней с даты заключения Договора.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.21.6
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Самостоятельно обеспечивать безопасность своих информационных ресурсов в сети интернет.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.21.7
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Предоставлять по требованию Банка доступ к разделам Ресурса с ограниченным доступом, не связанным с администрированием и сопровождением (VIP, Оптовое, Клубное обслуживание, и т.п.).
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.21.8
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Соблюдать правила пользования Личным кабинетом Интернет-эквайринга, размещенным на Официальном сайте Банка.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 4.2.22
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Важно, чтобы в пункте было прямо указано, что он про QR-код
TEXT: Предоставить Покупателю для считывания QR-код с целью осуществления оплаты Товаров/услуг с использованием SberPayQR/Плати QR
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 4.2.23
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Важно, чтобы в пункте было прямо указано, что он про QR-код
TEXT: Не изменять QR-код партнера в одностороннем порядке.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 4.2.24
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Важно, чтобы в пункте было прямо указано, что он про QR-API
TEXT: Использовать API и сведения, передаваемые посредством QR-API, в границах прав и функциональных возможностей такого API и его описания, изложенного в Порядке, условиях и ответственности сторон при подключении QR-API, размещенном на Официальном сайте Банка.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 4.2.25
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: Важно, чтобы в пункте было прямо указано, что он про QR-API
TEXT: При выявлении фактов или признаков нарушения безопасности использования QR-API и функциональных возможностей организации информационно-технологического взаимодействия немедленно приостановить использование API и оповестить об этом Банк любым из способов, указанных в п.2.3.1. – п.2.3.4. Договора.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 4.2.26
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: В дополнение к соблюдению требований, указанных в Условиях, в том числе приложениях к Условиям, в целях осуществлении Повторяющихся платежей Предприятие обязано:
• размещать на Ресурсе пользовательское соглашение/оферту Предприятия, содержащее условия проведения Повторяющихся платежей (в случае их применения), а также хранить письменное соглашение с Держателем (согласие/ поручение Держателя) об условиях проведения Повторяющихся платежей;
• получать от Держателя согласие на совершение Повторяющихся платежей, в том числе с суммой Повторяющихся платежей, периодом времени, в течение которого совершаются Повторяющиеся платежи, регулярностью совершения Повторяющихся платежей, порядком прекращения неосуществленных Повторяющихся платежей;
• обеспечить Покупателю возможность отмены неосуществленных Повторяющихся платежей в порядке, определенном договором, заключенным с Покупателем;
• прекратить осуществление Повторяющихся платежей в порядке, установленном договором с Покупателем, в случае получения от такого Покупателя уведомления об отказе от осуществления Повторяющихся платежей и (или) прекращения действия договора, заключенного между Покупателем и Предприятием.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 5
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: «ПРАВА И ОБЯЗАННОСТИ БАНКА»
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 5.1
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Банк имеет право:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 5.1.2
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: В случае возникновения у Предприятия задолженности перед Банком приостановить проведение Авторизации до момента полного погашения задолженности.
COMMENT: Обязательное требование применимо к торговому эквайрингу. Договор перечисляет иные основания приостановки авторизации, но не предоставляет Банку право приостанавливать авторизацию до полного погашения задолженности.

## M 5.1.4
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: При невозможности удержать суммы, указанные в п. 5.1.1 Договора, из сумм, подлежащих последующему перечислению Предприятию, Банк выставляет счет на оплату или платежное требование о списании денежных средств к расчетному счету Предприятия № --------------------------- в ----------------------- (наименование Банка, котором у Предприятия открыт счет).
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 5.1.5
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Не возмещать Предприятию суммы Операций, проведенных с нарушением условий Договора.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 5.1.7
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Независимо от срока действия Договора в случае выявления подозрительных или мошеннических Операций передавать информацию, в том числе осуществлять передачу данных (сведения о Предприятии/ТСТ/Ресурсе, в том числе персональные данные руководителя/представителя Предприятия, указанные в Заявлении/Информации о ТСТ) в Платежную систему МИР в целях исполнения запросов, полученных от указанной платежной системы.В случае принятия Банком решения о расторжении Договора по причине мошеннической деятельности Предприятия сообщать в Платежную систему МИР: даты заключения и расторжения Договора, а также причины расторжения Договора, иные сведения о Предприятии.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 5.1.8.4
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: несоответствие Ресурса Требованиям Банка к Ресурсу Предприятия;
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 5.1.8.5
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: осуществление видов деятельности, указанных в Требованиях к Ресурсу Предприятия;
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 5.1.9
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Осуществлять дополнительные проверки проведения Операции в ТСТ, в т.ч. обращаться в Банк-эмитент для проверки правомерности Операции.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 5.1.12
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: В одностороннем порядке вносить изменения в документы, ссылки на которые даются в Договоре, путем публикации информации на Официальном сайте Банка не менее чем за 1 (один) календарный день до введения в действие указанных изменений.
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 5.1.15
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Отказать Предприятию в заключении Договора без объяснения причин.
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 5.1.17.1
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['pos']
MAIN_IDEA: 
TEXT: Торговый оборот на один Электронный терминал Банка не превышает 40 000 (сорок тысяч) рублей (для г. Москва и г. Санкт-Петербург не превышает 80 000 (восемьдесят тысяч) рублей) за последний календарный месяц (без учета в обороте Операций возврата);
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 5.1.17.2
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['smart']
MAIN_IDEA: 
TEXT: Торговый оборот на один Смарт-терминал Банка не превышает 40 000 (сорок тысяч) рублей (для г. Москва и г. Санкт-Петербург не превышает 80 000 (восемьдесят тысяч) рублей) за последний календарный месяц (без учета в обороте Операций возврата) или имеется задолженность перед Банком по плате за сервисное обслуживание/вознаграждения Банка/Операциям возврата Смарт-терминала Банка.
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 5.2
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Банк обязуется:
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 5.2.1
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Обеспечить Предприятию доступ к СПЭП для осуществления Операций.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 5.2.2
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Обеспечить безопасность проведения Операций в Интернет-эквайринге посредством использования современных протоколов и Технологий 3DSecure
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 5.2.9
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: 
TEXT: Предоставить Предприятию QR-код партнера по электронным каналам связи в электронном виде или на бумажном носителе
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 5.2.12
CATEGORY: not_applicable
REQUIRED_TYPE: optional
SELECTORS: product=['trade_acquiring']; lot=[]; payment=['qr']; terminal=[]
MAIN_IDEA: 
TEXT: Банк предоставляет QR-API (в т.ч. всё, что связано с организацией информационно-технологического взаимодействия посредством такого API) «как есть» без предоставления каких-либо гарантий
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: payment_method=[qr], профиль=[нет выбранного значения].

## M 6
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: «ОПЛАТА УСЛУГ БАНКА»
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 6.7
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['pos']
MAIN_IDEA: 
TEXT: Плата за сервисное обслуживание Электронных терминалов уплачивается Предприятием ежемесячно в размере _____ (__________________) рублей (включая НДС) за каждый Электронный терминал.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 6.8
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['pos']
MAIN_IDEA: 
TEXT: Банк ежемесячно, не позднее 5 (пятого) рабочего дня месяца, следующего за Отчетным месяцем, направляет Предприятию УПД  на сумму платы за сервисное обслуживание Электронных терминалов за Отчетный месяц.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 6.9
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=['fz_223']; payment=[]; terminal=['pos']
MAIN_IDEA: 
TEXT: Банк ежемесячно, не позднее 10 (десятого) рабочего дня месяца, следующего за Отчетным месяцем, направляет Предприятию счет ф.363 на сумму платы за сервисное обслуживание Электронных терминалов за Отчетный месяц.
Счет-фактура предоставляется Банком в порядке и сроки, установленные действующим налоговым законодательством Российской Федерации
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_lot=[fz_223], профиль=[fz_44].

## M 6.10
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['pos']
MAIN_IDEA: 
TEXT: Предприятие в срок не позднее 25 (двадцать пятого) числа месяца, следующего за Отчетным месяцем, обязано возвратить в Банк подписанный Предприятием УПД на сумму платы за сервисное обслуживание Электронных терминалов за Отчетный месяц либо предоставить мотивированный отказ от подписания УПД.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 6.11
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['pos']
MAIN_IDEA: 
TEXT: Оплата за сервисное обслуживание Электронных терминалов за Отчетный месяц производится Предприятием в течение 5 (пяти) рабочих дней с даты подписания УПД в безналичной форме путем перечисления Предприятием денежных средств на счет Банка, указанный в УПД.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 6.12
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=['fz_223']; payment=[]; terminal=['pos']
MAIN_IDEA: 
TEXT: Оплата за сервисное обслуживание Электронных терминалов за Отчетный месяц производится Предприятием в течение 5 (пяти) рабочих дней с даты получения от Банка счета ф.363 в безналичной форме путем перечисления Предприятием денежных средств на счет Банка, указанный в счете ф.363.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_lot=[fz_223], профиль=[fz_44].

## M 6.13
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['smart']
MAIN_IDEA: 
TEXT: Плата за сервисное обслуживание Смарт-терминалов уплачивается Предприятием ежемесячно за каждый Смарт-терминал в соответствии с тарифом, установленным при заключении Договора в Приложении 1.1 к Договору. При этом расчет платы осуществляется пропорционально количеству календарных дней месяца с даты установки Смарт-терминала в ТСТ. При подключении/отключении 2D сканера размер платы за Смарт-терминал пересчитывается за Отчетный месяц независимо от даты установки/снятия сканера:
           - в размере _____ (__________________) рублей (включая НДС) за каждый Смарт-терминал;
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 6.14
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['smart']
MAIN_IDEA: 
TEXT: Банк ежемесячно, не позднее 5 (пятого) рабочего дня месяца, следующего за Отчетным месяцем, направляет Предприятию УПД  на сумму платы за сервисное обслуживание Смарт-терминалов за Отчетный месяц.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 6.15
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=['fz_223']; payment=[]; terminal=['smart']
MAIN_IDEA: 
TEXT: Банк ежемесячно, не позднее 10 (десятого) рабочего дня месяца, следующего за Отчетным месяцем, направляет Предприятию счет ф.363 на сумму платы за сервисное обслуживание Смарт-терминалов за Отчетный месяц.
Счет-фактура предоставляется Банком в порядке и сроки, установленные действующим налоговым законодательством Российской Федерации.
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_lot=[fz_223], профиль=[fz_44].

## M 6.16
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['smart']
MAIN_IDEA: 
TEXT: Предприятие в срок не позднее 25 (двадцать пятого) числа месяца, следующего за Отчетным месяцем, обязано возвратить в Банк подписанный Предприятием УПД на сумму платы за сервисное обслуживание Смарт-терминалов за Отчетный месяц либо предоставить мотивированный отказ от подписания УПД.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 6.17
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=[]; payment=[]; terminal=['smart']
MAIN_IDEA: 
TEXT: Оплата за сервисное обслуживание Смарт-терминалов за Отчетный месяц производится Предприятием в течение 5 (пяти) рабочих дней с даты подписания УПД в безналичной форме путем перечисления Предприятием денежных средств на счет Банка, указанный в УПД.
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 6.18
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['trade_acquiring']; lot=['fz_223']; payment=[]; terminal=['smart']
MAIN_IDEA: 
TEXT: Оплата за сервисное обслуживание Смарт-терминалов за Отчетный месяц производится Предприятием в течение 5 (пяти) рабочих дней с даты получения от Банка счета ф.363 в безналичной форме путем перечисления Предприятием денежных средств на счет Банка, указанный в счете ф.363
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_lot=[fz_223], профиль=[fz_44].

## M 7
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: РАЗДЕЛ 7 «ОТВЕТСТВЕННОСТЬ СТОРОН»
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 7.8
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: Важен факт, что Банк (Исполнитель) не несет ответственности по спорам и разногласиям Предприятия(Заказчика) и Покупателя (клиент Предприятия)
TEXT: Банк не несет ответственности по спорам и разногласиям, возникающим между Предприятием и Покупателем во всех случаях, когда такие споры и разногласия не относятся к предмету Договора, а также по спорам в отношении Товаров/услуг, оплаченных с использованием Карты / ее реквизитов / NFС-карты / SberPay / Плати QR / Платежного счета.
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 7.9
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Банк не несет ответственности за задержки перевода денежных средств на счет Предприятия, если задержки произошли не по вине Банка.
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 7.10
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Банк не несет ответственности за неисполнение условий Договора, обусловленное действиями или бездействиями третьих лиц, в том числе участниками Платежной системы
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 7.11
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Банк не несет ответственности за несвоевременное перечисление сумм Операций по причине проведения расследования Банком при подозрении на проведение Операции с нарушением условий Договора
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 7.12
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Банк не несет ответственности в случае превышения установленной Цены Договора
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 7.13
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Предприятие несет ответственность за некорректность проведенных Операций, совершенных на Ресурсе, в случае невыполнения п. 4.2.21.1 Договора
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 7.14
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Предприятие несет ответственность за все действия, осуществляемые Предприятием/ТСТ в СПЭП
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 7.17
CATEGORY: not_applicable
REQUIRED_TYPE: mandatory
SELECTORS: product=['internet_acquiring']; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: Важно, чтобы  в договоре не было сужения ответственности.
TEXT: Предприятие несет полную финансовую ответственность перед Банком в случае несоответствия проведенных Повторяющихся платежей требованиям законодательства Российской Федерации, в том числе нормативных актов Банка России, правил Платежной системы и (или) Договора
COMMENT: Селекторы требования не совпадают с подтверждённым профилем договора: only_for_product=[internet_acquiring], профиль=[trade_acquiring].

## M 8
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: РАЗДЕЛ 8 «ФОРС-МАЖОРНЫЕ ОБСТОЯТЕЛЬСТВА»
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 9
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: РАЗДЕЛ 9 «УРЕГУЛИРОВАНИЕ СПОРОВ»
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 10
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: РАЗДЕЛ 10 «СРОК ДЕЙСТВИЯ ДОГОВОРА И ПОРЯДОК ЕГО РАСТОРЖЕНИЯ
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 10.3
CATEGORY: missing_in_contract
REQUIRED_TYPE: mandatory
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: При расторжении настоящего Договора Банком в одностороннем внесудебном порядке в случаях, предусмотренных п. 5.1.8. настоящего Договора, Договор считается расторгнутым с даты, указанной в письменном уведомлении Банка о расторжении. Стороны осуществляют расчеты/взаиморасчеты в течение 18 (восемнадцать) месяцев с даты расторжения Договора. Предприятие выплачивает Банку суммы Операций в порядке, установленном п.п. 5.1.3, 5.1.4 и разделом 6 настоящего Договора
COMMENT: Требование применимо по селекторам, имеет required_type=mandatory и юридического аналога в договоре не найдено.

## M 11
CATEGORY: structural
REQUIRED_TYPE: None
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: РАЗДЕЛ 11 «ПРОЧИЕ УСЛОВИЯ
COMMENT: Поле required_type отсутствует: строка является структурным заголовком матрицы и не образует самостоятельного требования.

## M 11.3
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Стороны обязуются не разглашать полученные в ходе исполнения Договора сведения, включая:
• описание защитных элементов Карт;
• технологию проведения Операций;
• информацию об управлении, финансовой и иной деятельности Сторон;
• иную информацию, разглашение которой может привести к возникновению убытков или негативно повлиять на деловую репутацию Сторон.
Предоставление указанной информации допускается только при согласии обеих Сторон.
Данное положение не отменяет п. 5.1.7 Договора
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 11.5
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Все имевшие место до подписания настоящего Договора соглашения, переговоры и переписка между Сторонами по вопросам, изложенным в настоящем Договоре, утрачивают силу с даты подписания настоящего Договора.
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 11.6
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Настоящий Договор составлен в ________ экземплярах, _________ экземпляр(-а) для Банка, один экземпляр для Предприятия
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 11.8
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: Предприятие заверяет, что реализация Товаров/услуг в ТСТ осуществляется в соответствии с требованиями действующего законодательства РФ
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 11.11
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: Обязательность инструктивных материалов, размещённых на официальном сайте Банка, и момент вступления их в силу
TEXT: Инструктивные материалы, касающиеся предмета Договора, включая документы, ссылки на которые даются в настоящем Договоре, размещенные на Официальном сайте Банка, становятся обязательными к исполнению со следующего рабочего дня за днем размещения их на Официальном сайте Банка: https://www.sberbank.ru, если не указаны иные сроки ввода их в действие
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.

## M 11.13
CATEGORY: optional_absent
REQUIRED_TYPE: optional
SELECTORS: product=[]; lot=[]; payment=[]; terminal=[]
MAIN_IDEA: 
TEXT: К настоящему Договору прилагаются:
1) Приложение № 1 – Заявление Предприятия на проведение расчетов по операциям оплаты товаров/услуг.
2) Приложение № 1.1 – Информация о Торгово-Сервисной Точке/Ресурсе Предприятия.
3) Приложение № 2 – Акт о перечислении Предприятию сумм операций по картам.
Приложение № 3 – Описание услуг, формирующих тарифы и параметры сервиса для Смарт-терминалов
COMMENT: Требование применимо по селекторам, но имеет required_type=optional и юридического аналога в договоре не найдено.