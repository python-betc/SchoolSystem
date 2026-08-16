import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# استيراد التطبيق وقاعدة البيانات من ملفك الرئيسي
from app import app, db, Student, TELEGRAM_BOT_TOKEN

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب بولي الأمر"""
    msg = (
        "مرحباً بك في نظام المتابعة المدرسية! 🏫\n\n"
        "يرجى إرسال **كود الطالب** الخاص بابنك (مثال: STU-1001) لربط حسابك وتلقي الإشعارات التلقائية."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_student_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال الكود ورابط الحساب بقاعدة البيانات"""
    user_code = update.message.text.strip().upper()
    chat_id = str(update.message.chat_id)

    # التقاط بيانات حساب تليجرام
    telegram_user = update.message.from_user
    telegram_username = telegram_user.username

    # تكوين الاسم الظاهر لولي الأمر في تليجرام (الاسم الأول + الأخير إن وجد)
    telegram_name = telegram_user.first_name
    if telegram_user.last_name:
        telegram_name += f" {telegram_user.last_name}"

    with app.app_context():
        student = Student.query.filter_by(student_code=user_code).first()

        if student:
            student.parent_telegram_id = chat_id
            student.parent_telegram_username = telegram_username
            student.parent_telegram_name = telegram_name  # <-- حفظ الاسم الظاهر لحساب تليجرام
            db.session.commit()

            response = (
                f"✅ **تم ربط الحساب بنجاح!**\n\n"
                f"👤 **اسم الطالب:** {student.name}\n"
                f"📚 **الصف:** {student.grade_class}\n\n"
                f"ستصلك الآن تنبيهات الحضور والغياب والسلوك الخاصة بابنك فور حدوثها."
            )
        else:
            response = "❌ كود الطالب غير صحيح! يرجى التأكد من الكود وإعادة إرساله."

    await update.message.reply_text(response, parse_mode="Markdown")


if __name__ == '__main__':
    print("🤖 يتم الآن تشغيل بوت تليجرام للتفاعل مع أولياء الأمور...")
    bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_student_code))

    bot_app.run_polling()