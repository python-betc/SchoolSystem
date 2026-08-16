import atexit
import io
import os
import base64
import qrcode
from io import BytesIO
import random
import string
from datetime import date, datetime, timedelta
from functools import wraps
from sqlalchemy import func
from apscheduler.schedulers.background import BackgroundScheduler
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import requests
from werkzeug.security import check_password_hash, generate_password_hash

# =========================================================================
# 1. إعدادات التطبيق وقاعدة البيانات وبوت التليجرام
# =========================================================================
app = Flask(__name__)

database_url = os.environ.get("DATABASE_URL")

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}
if database_url:
    # تعديل رابط قاعدة البيانات إذا كان يبدأ بـ postgres:// ليناسب SQLAlchemy
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    # القيمة الافتراضية للعمل محلياً
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///school.db"
# --- نهاية التعديل ---

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "super_secret_key_12345"

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# توكن بوت التليجرام الخاص بالإشعارات
TELEGRAM_BOT_TOKEN = "8810713846:AAH8sM_8Hjf60U06BJLxqz9pKUMB1urBXd0"


# =========================================================================
# 2. نماذج قاعدة البيانات (Database Models المتكاملة والقوية)
# =========================================================================


class User(db.Model, UserMixin):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)

    # وقت انتهاء صلاحية المسح المخصصة لهذا المعلم
    gate_scan_unlock_until = db.Column(db.DateTime, nullable=True)

    assignments = db.relationship(
        "TeacherAssignment",
        backref="teacher",
        cascade="all, delete-orphan",
        lazy=True,
    )
    class_attendances = db.relationship(
        "ClassAttendance", backref="teacher", lazy=True
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Parent(db.Model):
    """جدول أولياء الأمور (رقم فريد لدعم عدة أبناء لنفس ولي الأمر وتليجرام موحد)."""

    __tablename__ = "parent"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    telegram_id = db.Column(db.String(50), unique=True, nullable=True)
    telegram_username = db.Column(db.String(100), nullable=True)

    # علاقة تتيح معرفة جميع أبناء ولي الأمر في المدرسة بسهولة تامة
    students = db.relationship("Student", backref="parent", lazy=True)


class SchoolClass(db.Model):
    """جدول الصفوف والشعب الدراسية."""

    __tablename__ = "school_class"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)

    students = db.relationship("Student", backref="school_class_obj", lazy=True)
    assignments = db.relationship(
        "TeacherAssignment",
        backref="school_class",
        cascade="all, delete-orphan",
        lazy=True,
    )


class Subject(db.Model):
    """جدول المواد التعليمية."""

    __tablename__ = "subject"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    assignments = db.relationship(
        "TeacherAssignment",
        backref="subject",
        cascade="all, delete-orphan",
        lazy=True,
    )


class Student(db.Model):
    """جدول بيانات الطلاب والإحصائيات التراكمية للحضور والنقاط مع ربط محكم بالصف ولي الأمر."""

    __tablename__ = "student"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    student_code = db.Column(db.String(30), unique=True, nullable=False)

    # الربط الأساسي السليم مع جدول الصفوف ومع الاحتفاظ بالنص للتوافقية
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=True)
    grade_class = db.Column(db.String(50), nullable=False)

    # الربط الاحترافي بجدول أولياء الأمور
    parent_id = db.Column(db.Integer, db.ForeignKey("parent.id"), nullable=True)

    # حقول توافقية لتليجرام ولي الأمر مباشرة للسرعة والتقارير
    parent_telegram_id = db.Column(db.String(50), nullable=True)
    parent_telegram_username = db.Column(db.String(100), nullable=True)
    parent_telegram_name = db.Column(db.String(150), nullable=True)

    points = db.Column(db.Integer, default=100)

    absent_periods_count = db.Column(db.Integer, default=0)
    absent_days_count = db.Column(db.Integer, default=0)
    warning_sent = db.Column(db.Boolean, default=False)

    gate_attendances = db.relationship(
        "Attendance",
        backref="student",
        cascade="all, delete-orphan",
        lazy=True,
    )
    class_attendances = db.relationship(
        "ClassAttendance",
        backref="student",
        cascade="all, delete-orphan",
        lazy=True,
    )
    behavior_logs = db.relationship(
        "BehaviorLog",
        backref="student",
        cascade="all, delete-orphan",
        lazy=True,
    )
    notifications = db.relationship(
        "ParentNotification",
        backref="student",
        cascade="all, delete-orphan",
        lazy=True,
    )


class TeacherAssignment(db.Model):
    """جدول إسناد المواد والصفوف للمعلمين (دائم طوال الفصل الدراسي)."""

    __tablename__ = "teacher_assignment"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    class_id = db.Column(
        db.Integer, db.ForeignKey("school_class.id"), nullable=False
    )
    subject_id = db.Column(
        db.Integer, db.ForeignKey("subject.id"), nullable=False
    )

    temporary_assignments = db.relationship(
        "TemporaryAssignment",
        backref="assignment",
        cascade="all, delete-orphan",
        lazy=True,
    )


class TemporaryAssignment(db.Model):
    """جدول إسناد حصص المعلم الغائب لمعلم بديل مؤقتاً ليوم واحد فقط."""

    __tablename__ = "temporary_assignment"

    id = db.Column(db.Integer, primary_key=True)
    original_teacher_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    substitute_teacher_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    assignment_id = db.Column(
        db.Integer, db.ForeignKey("teacher_assignment.id"), nullable=False
    )
    date = db.Column(db.Date, default=date.today)

    original_teacher = db.relationship(
        "User", foreign_keys=[original_teacher_id]
    )
    substitute_teacher = db.relationship(
        "User", foreign_keys=[substitute_teacher_id]
    )


class Attendance(db.Model):
    """جدول سجلات الحضور عند بوابة المدرسة."""

    __tablename__ = "attendance"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer, db.ForeignKey("student.id"), nullable=False
    )
    type = db.Column(db.String(20), default="gate")  # 'gate'
    timestamp = db.Column(db.DateTime, default=datetime.now)


class ClassAttendance(db.Model):
    """جدول الحضور داخل الحصص الدراسية والتقييم السلوكي."""

    __tablename__ = "class_attendance"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer, db.ForeignKey("student.id"), nullable=False
    )
    teacher_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    subject_name = db.Column(db.String(50))
    class_name = db.Column(db.String(50))
    type = db.Column(
        db.String(20), nullable=False
    )  # 'gate', 'class_attendance', 'behavior_eval'
    points_change = db.Column(db.Integer, default=0)
    note = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.now)


class BehaviorLog(db.Model):
    """جدول سجل السلوك النقاطي التراكمي للطالب."""

    __tablename__ = "behavior_log"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer, db.ForeignKey("student.id"), nullable=False
    )
    points_change = db.Column(db.Integer)
    reason = db.Column(db.String(200))
    teacher_name = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.now)


class SystemSettings(db.Model):
    """جدول إعدادات النظام للغياب والتنبيهات وعدد الحصص الفعالة اليومية."""

    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)
    periods_per_absent_day = db.Column(db.Integer, default=7)
    max_absent_days_warning = db.Column(db.Integer, default=15)
    daily_actual_lessons = db.Column(
        db.Integer, default=6
    )  # عدد الحصص الفعلية اليوم
    gate_scan_lock_until = (
        db.Column(db.DateTime, nullable=True)
    )  # وقت انتهاء القفل عامة
    gate_cutoff_time = db.Column(
        db.String(5), default="08:30"
    )  # وقت قفل البوابة اليومي


class ParentNotification(db.Model):
    """جدول أرشفة الإشعارات الموجهة لأولياء الأمور."""

    __tablename__ = "parent_notification"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer, db.ForeignKey("student.id"), nullable=False
    )
    title = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    date = db.Column(db.DateTime, default=datetime.now)


# =========================================================================
# 3. الدوال المساعدة والأدوات (Helper Functions)
# =========================================================================


def send_telegram_msg(chat_id, text):
    """إرسال رسالة فورية عبر بوت التليجرام إلى ولي الأمر."""
    if not chat_id:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": str(chat_id).strip(),
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200 and response.json().get("ok"):
            return True
        else:
            print(f"خطأ من تليجرام: {response.text}")
            return False
    except Exception as e:
        print(f"خطأ أثناء الإرسال عبر تليجرام: {e}")
        return False


def send_parent_warning_notification(student):
    """إرسال تنبيه رسمي لولي الأمر وتسجيل الإشعار عند تجاوز حد الغياب المسموح."""
    msg_title = "تنبيه هام: تجاوز حد الغياب المسموح"
    msg_body = (
        f"عزيزي ولي أمر الطالب ({student.name})، نود إعلامكم بأن مجموع أيام غياب الطالب قد بلغ "
        f"({student.absent_days_count}) يوماً. يُرجى الحضور إلى إدارة المدرسة لمراجعة موقف الطالب."
    )

    new_notice = ParentNotification(
        student_id=student.id,
        title=msg_title,
        message=msg_body,
        date=datetime.now(),
    )
    db.session.add(new_notice)

    target_chat_id = student.parent_telegram_id
    if not target_chat_id and student.parent and student.parent.telegram_id:
        target_chat_id = student.parent.telegram_id

    if target_chat_id:
        send_telegram_msg(
            target_chat_id, f"🚨 *{msg_title}*\n\n{msg_body}"
        )


def check_and_process_absence(student):
    """احتساب غياب الحصص وتحويله لأيام كاملة وتطبيق قوانين الإنذار."""
    settings = SystemSettings.query.first()
    periods_limit = settings.periods_per_absent_day if settings else 7
    days_limit = settings.max_absent_days_warning if settings else 15

    student.absent_periods_count += 1

    if student.absent_periods_count >= periods_limit:
        new_days = student.absent_periods_count // periods_limit
        student.absent_days_count += new_days
        student.absent_periods_count = (
                student.absent_periods_count % periods_limit
        )

    if student.absent_days_count >= days_limit and not student.warning_sent:
        send_parent_warning_notification(student)
        student.warning_sent = True

    db.session.commit()


def generate_student_code():
    """توليد رمز فريد ومميز للطالب بشكل أوتوماتيكي (مثل STU-1234)."""
    while True:
        code = "STU-" + "".join(random.choices(string.digits, k=4))
        if not Student.query.filter_by(student_code=code).first():
            return code


# =========================================================================
# 4. دالات الحماية والتحقق من الصلاحيات (Decorators & Authentication)
# =========================================================================


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            flash("عذراً، هذه الصفحة مخصصة للإدارة فقط.", "danger")
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def teacher_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in [
            "teacher",
            "admin",
        ]:
            flash("عذراً، هذه الصفحة مخصصة للمعلمين.", "danger")
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


# =========================================================================
# 5. مسارات المصادقة والتوجيه الرئيسي (Auth Routes)
# =========================================================================


@app.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("teacher_portal"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if current_user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("teacher_portal"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            flash(f"أهلاً بك {user.name}", "success")
            if user.role == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("teacher_portal"))
        else:
            flash("اسم المستخدم أو كلمة المرور غير صحيحة", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("تم تسجيل الخروج بنجاح", "info")
    return redirect(url_for("login"))


# =========================================================================
# 6. مسارات الإدارة والتحكم (Admin Routes)
# =========================================================================

import requests  # تأكد من استيراد مكتبة requests في أعلى الملف إذا لم تكن موجودة

@app.route("/admin_dashboard")
@admin_required
def admin_dashboard():
    # جلب معرف البوت تلقائياً من تليجرام لربطه بالقالب
    bot_username = "Abd_AlMalik_BinMarwan_School"
    try:
        res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=2)
        if res.status_code == 200 and res.json().get("ok"):
            bot_username = res.json()["result"]["username"]
    except Exception:
        pass

    today = date.today()
    settings = SystemSettings.query.first()
    target_lessons = settings.daily_actual_lessons if settings else 6

    total_students = Student.query.count()

    gate_attendance_ids = [
        a.student_id
        for a in Attendance.query.filter(
            Attendance.type == "gate",
            db.func.date(Attendance.timestamp) == today,
        ).all()
    ]

    teacher_gate_ids = [
        a.student_id
        for a in ClassAttendance.query.filter(
            ClassAttendance.type == "gate",
            db.func.date(ClassAttendance.timestamp) == today,
        ).all()
    ]

    all_present_ids = set(gate_attendance_ids + teacher_gate_ids)
    present_count = len(all_present_ids)
    absent_count = total_students - present_count

    if all_present_ids:
        absent_students_list = (
            Student.query.filter(Student.id.notin_(all_present_ids))
            .order_by(Student.grade_class.asc(), Student.name.asc())
            .all()
        )
    else:
        absent_students_list = Student.query.order_by(
            Student.grade_class.asc(), Student.name.asc()
        ).all()

    leakers_list = []
    for student_id in all_present_ids:
        st = Student.query.get(student_id)
        if not st:
            continue

        attended_count = ClassAttendance.query.filter(
            ClassAttendance.student_id == st.id,
            ClassAttendance.type == "class_attendance",
            db.func.date(ClassAttendance.timestamp) == today,
        ).count()

        if attended_count < target_lessons:
            leakers_list.append({
                "student": st,
                "attended": attended_count,
                "target": target_lessons,
                "missing": target_lessons - attended_count,
            })

    period_attended_count = (
        db.session.query(ClassAttendance.student_id)
        .filter(
            ClassAttendance.type == "class_attendance",
            db.func.date(ClassAttendance.timestamp) == today,
        )
        .distinct()
        .count()
    )

    students = Student.query.order_by(
        Student.grade_class.asc(), Student.name.asc()
    ).all()

    # استخدام Attendance بدلاً من ClassAttendance لقراءة الغياب التام
    absences_query = db.session.query(
        Attendance.student_id,
        db.func.count(db.func.distinct(db.func.date(Attendance.timestamp)))
    ).filter(
        Attendance.type == "absence"
    ).group_by(Attendance.student_id).all()

    student_absences = {student_id: count for student_id, count in absences_query}

    teachers = User.query.filter_by(role="teacher").all()
    assignments = TeacherAssignment.query.all()
    temp_assignments = TemporaryAssignment.query.filter_by(date=today).all()

    return render_template(
        "admin_dashboard.html",
        now=datetime.now(),
        total=total_students,
        present=present_count,
        absent=absent_count,
        period_present=period_attended_count,
        absent_students=absent_students_list,
        leakers_students=leakers_list,
        students=students,
        student_absences=student_absences,
        settings=settings,
        teachers=teachers,
        assignments=assignments,
        temp_assignments=temp_assignments,
        bot_username=bot_username,  # تمرير معرف البوت هنا بنجاح
    )


@app.route("/admin/notify_parent", methods=["POST"])
@admin_required
def notify_parent():
    student_id = request.form.get("student_id")
    msg_type = request.form.get("msg_type")

    if not student_id:
        flash("خطأ في تحديد الطالب!", "danger")
        return redirect("/admin_dashboard")

    student = Student.query.get(student_id)
    chat_id = student.parent_telegram_id if (student and student.parent_telegram_id) else (
        student.parent.telegram_id if student and student.parent else None)

    if not student or not chat_id:
        flash(
            f"الطالب {student.name if student else ''} غير مربوط بحساب تليجرام لولي الأمر!",
            "warning",
        )
        return redirect("/admin_dashboard")

    today_str = date.today().strftime("%Y-%m-%d")

    if msg_type == "absence":
        alert_msg = (
            f"تنبيه بعدم الحضور الى المدرسة\n\n"
            f" *الطالب:* {student.name}\n"
            f" *الصف:* {student.grade_class}\n"
            f" *التاريخ:* {today_str}\n\n"
            f"⚠️ *تفاصيل:* نفيدكم بعدم تسجيل دخول الطالب عبر بوابة المدرسة اليوم حتى الآن. يرجى المتابعة."
        )
    else:
        alert_msg = (
            f"⚠️ *تنبيه عدم اكتمال الحصص (تسرب)*\n\n"
            f"👤 *الطالب:* {student.name}\n"
            f"🏫 *الصف:* {student.grade_class}\n"
            f"📅 *التاريخ:* {today_str}\n\n"
            f"📌 *تفاصيل:* سجل الطالب دخولاً عند البوابة ولكنه غائب عن بعض الحصص الدراسية المقررة اليوم."
        )

    success = send_telegram_msg(chat_id, alert_msg)

    if success:
        flash(
            f"تم إرسال تنبيه الـ Telegram لولي أمر الطالب ({student.name}) بنجاح! 📲",
            "success",
        )
    else:
        flash(
            f"فشل إرسال الرسالة عبر التليجرام، تحقق من اتصال البوت.",
            "danger",
        )

    return redirect("/admin_dashboard")


@app.route("/admin/notify_all_absent", methods=["POST"])
@admin_required
def notify_all_absent():
    today = date.today()
    gate_ids = [
        a.student_id
        for a in Attendance.query.filter(
            Attendance.type == "gate",
            db.func.date(Attendance.timestamp) == today,
        ).all()
    ]
    teacher_gate_ids = [
        a.student_id
        for a in ClassAttendance.query.filter(
            ClassAttendance.type == "gate",
            db.func.date(ClassAttendance.timestamp) == today,
        ).all()
    ]

    all_present_ids = set(gate_ids + teacher_gate_ids)
    absent_students = Student.query.filter(
        Student.id.notin_(all_present_ids)
    ).all() if all_present_ids else Student.query.all()

    sent_count = 0
    for st in absent_students:
        chat_id = st.parent_telegram_id or (st.parent.telegram_id if st.parent else None)
        if chat_id:
            alert_msg = (
                f"تنبيه بعدم الحضور إلى المدرسة\n\n"
                f" *الطالب:* {st.name}\n\n"
                f" *الصف:* {st.grade_class}\n\n"
                f" *التاريخ:* {today.strftime('%Y-%m-%d')}\n\n"
                f"⚠️ *تفاصيل:* نفيدكم بعدم تسجيل دخول الطالب عبر بوابة المدرسة اليوم."
            )
            if send_telegram_msg(chat_id, alert_msg):
                sent_count += 1

    flash(
        f"تم إرسال إشعارات الغياب بنجاح إلى ({sent_count}) من أولياء الأمور! 📲",
        "success",
    )
    return redirect("/admin_dashboard")


@app.route("/admin/substitute_teacher", methods=["POST"])
@admin_required
def assign_substitute_teacher():
    assignment_id = request.form.get("assignment_id")
    substitute_teacher_id = request.form.get("substitute_teacher_id")
    today = date.today()

    target_assignment = TeacherAssignment.query.get(assignment_id)
    if not target_assignment or not substitute_teacher_id:
        flash("بيانات غير صحيحة للإسناد البديل!", "danger")
        return redirect(url_for("admin_dashboard"))

    existing_temp = TemporaryAssignment.query.filter_by(
        assignment_id=assignment_id, date=today
    ).first()

    if existing_temp:
        existing_temp.substitute_teacher_id = substitute_teacher_id
    else:
        new_temp = TemporaryAssignment(
            original_teacher_id=target_assignment.teacher_id,
            substitute_teacher_id=substitute_teacher_id,
            assignment_id=assignment_id,
            date=today,
        )
        db.session.add(new_temp)

    db.session.commit()
    sub_teacher = User.query.get(substitute_teacher_id)
    flash(
        f"✅ تم إسناد الصف {target_assignment.school_class.name} - مادة {target_assignment.subject.name} للمعلم البديل ({sub_teacher.name}) لليوم فقط.",
        "success",
    )
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/update_attendance_settings", methods=["POST"])
@admin_required
def update_attendance_settings():
    periods = int(request.form.get("periods_per_absent_day", 7))
    max_days = int(request.form.get("max_absent_days_warning", 15))
    actual_lessons = int(request.form.get("daily_actual_lessons", 6))

    settings = SystemSettings.query.first()
    if not settings:
        settings = SystemSettings(
            periods_per_absent_day=periods,
            max_absent_days_warning=max_days,
            daily_actual_lessons=actual_lessons,
        )
        db.session.add(settings)
    else:
        settings.periods_per_absent_day = periods
        settings.max_absent_days_warning = max_days
        settings.daily_actual_lessons = actual_lessons

    db.session.commit()
    flash("تم تحديث لائحة الغياب وعدد حصص اليوم بنجاح!", "success")
    return redirect(url_for("admin_dashboard"))

import telegram
from telegram import Update
from flask import request

# تعريف كائن البوت
bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """مسار استقبال الرسائل من تليجرام وتوجيهها لقاعدة البيانات"""
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, bot)
        
        if update.message and update.message.text:
            chat_id = str(update.message.chat_id)
            text = update.message.text.strip()
            
            with app.app_context():
                if text.startswith("/start"):
                    msg = (
                        "مرحباً بك في نظام المتابعة المدرسية! 🏫\n\n"
                        "يرجى إرسال **كود الطالب** الخاص بابنك (مثال: STU-1001) لربط حسابك وتلقي الإشعارات التلقائية."
                    )
                    bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                else:
                    user_code = text.upper()
                    student = Student.query.filter_by(student_code=user_code).first()

                    if student:
                        student.parent_telegram_id = chat_id
                        telegram_user = update.message.from_user
                        student.parent_telegram_username = telegram_user.username
                        
                        telegram_name = telegram_user.first_name
                        if telegram_user.last_name:
                            telegram_name += f" {telegram_user.last_name}"
                        student.parent_telegram_name = telegram_name
                        db.session.commit()

                        response = (
                            f"✅ **تم ربط الحساب بنجاح!**\n\n"
                            f"👤 **اسم الطالب:** {student.name}\n"
                            f"📚 **الصف:** {student.grade_class}\n\n"
                            f"ستصلك الآن تنبيهات الحضور والغياب والسلوك الخاصة بابنك فور حدوثها."
                        )
                    else:
                        response = "❌ كود الطالب غير صحيح! يرجى التأكد من الكود وإعادة إرساله."

                    bot.send_message(chat_id=chat_id, text=response, parse_mode="Markdown")
                    
    except Exception as e:
        print(f"Error in webhook: {e}")
        
    return "OK", 200

@app.route("/admin/import_students", methods=["POST"])
@admin_required
def import_students():
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("يرجى اختيار ملف Excel أو CSV أولاً", "danger")
        return redirect(url_for("students_manage"))

    try:
        file_bytes = file.read()
        file_stream = io.BytesIO(file_bytes)

        if file.filename.endswith(".csv"):
            try:
                df = pd.read_csv(file_stream, encoding="utf-8-sig")
            except UnicodeDecodeError:
                file_stream.seek(0)
                df = pd.read_csv(file_stream, encoding="cp1256")
        else:
            df = pd.read_excel(file_stream)

        def clean_val(val):
            if val is None or pd.isna(val):
                return ""
            if isinstance(val, bytes):
                val = val.decode("utf-8", "ignore")
            s = str(val).strip()
            if s.lower() in ["nan", "none", "nat", ""]:
                return ""
            return s

        classes_cache = {c.name: c.id for c in SchoolClass.query.all()}
        parents_cache = {p.telegram_id: p.id for p in Parent.query.all() if p.telegram_id}

        added_count = 0
        for _, row in df.iterrows():
            name = (
                clean_val(row.get("اسم الطالب"))
                or clean_val(row.get("الاسم"))
                or clean_val(row.get("name"))
            )
            grade_class = (
                clean_val(row.get("الصف والشعبة"))
                or clean_val(row.get("الصف"))
                or clean_val(row.get("grade_class"))
            )
            telegram_id = (
                clean_val(row.get("تليجرام"))
                or clean_val(row.get("telegram_id"))
            )

            if not name or not grade_class:
                continue

            class_id = classes_cache.get(grade_class)
            if not class_id:
                new_class = SchoolClass(name=grade_class)
                db.session.add(new_class)
                db.session.flush()
                class_id = new_class.id
                classes_cache[grade_class] = class_id

            parent_id = None
            if telegram_id and telegram_id != "0":
                parent_id = parents_cache.get(telegram_id)
                if not parent_id:
                    new_parent = Parent(telegram_id=telegram_id, name=f"ولي أمر {name}")
                    db.session.add(new_parent)
                    db.session.flush()
                    parent_id = new_parent.id
                    parents_cache[telegram_id] = parent_id

            auto_code = str(generate_student_code())

            new_student = Student(
                student_code=auto_code,
                name=name,
                grade_class=grade_class,
                class_id=class_id,
                parent_id=parent_id,
                parent_telegram_id=telegram_id if telegram_id and telegram_id != "0" else None,
            )
            db.session.add(new_student)
            added_count += 1

        db.session.commit()
        flash(
            f"تمت إضافة {added_count} طالب بنجاح!",
            "success",
        )

    except Exception as e:
        db.session.rollback()
        flash(f"حدث خطأ أثناء قراءة الملف أو حفظ البيانات: {str(e)}", "danger")

    return redirect(url_for("students_manage"))


@app.route("/admin/assignments", methods=["GET", "POST"])
@admin_required
def manage_assignments():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_class":
            class_name = request.form.get("class_name", "").strip()
            if class_name:
                if SchoolClass.query.filter_by(name=class_name).first():
                    flash(f'⚠️ الصف "{class_name}" موجود بالفعل!', "warning")
                else:
                    db.session.add(SchoolClass(name=class_name))
                    db.session.commit()
                    flash(f'✅ تم إضافة الصف "{class_name}" بنجاح.', "success")

        elif action == "add_subject":
            subject_name = request.form.get("subject_name", "").strip()
            if subject_name:
                if Subject.query.filter_by(name=subject_name).first():
                    flash(f'⚠️ المادة "{subject_name}" موجودة بالفعل!', "warning")
                else:
                    db.session.add(Subject(name=subject_name))
                    db.session.commit()
                    flash(
                        f'✅ تم إضافة المادة "{subject_name}" بنجاح.', "success"
                    )

        elif action in ["assign", "assign_teacher"]:
            teacher_id = request.form.get("teacher_id")
            class_id = request.form.get("class_id")
            subject_id = request.form.get("subject_id")

            if teacher_id and class_id and subject_id:
                existing_assignment = TeacherAssignment.query.filter_by(
                    teacher_id=teacher_id,
                    class_id=class_id,
                    subject_id=subject_id,
                ).first()

                if existing_assignment:
                    flash(
                        "⚠️ هذا الإسناد موجود بالفعل لهذا المعلم!", "warning"
                    )
                else:
                    assignment = TeacherAssignment(
                        teacher_id=teacher_id,
                        class_id=class_id,
                        subject_id=subject_id,
                    )
                    db.session.add(assignment)
                    db.session.commit()
                    flash("✅ تم إسناد المادة والصف للمعلم بنجاح.", "success")

        return redirect(url_for("manage_assignments"))

    teachers = User.query.filter_by(role="teacher").all()
    subjects = Subject.query.all()
    classes = SchoolClass.query.all()
    assignments = TeacherAssignment.query.all()

    template_name = (
        "manage_assignments.html"
        if os.path.exists("templates/manage_assignments.html")
        else "assignments.html"
    )

    return render_template(
        template_name,
        teachers=teachers,
        subjects=subjects,
        classes=classes,
        assignments=assignments,
    )


@app.route("/admin/delete_class/<int:class_id>", methods=["POST"])
@app.route("/admin/classes/delete/<int:class_id>", methods=["POST"])
@admin_required
def delete_class(class_id):
    c = SchoolClass.query.get_or_404(class_id)
    db.session.delete(c)
    db.session.commit()
    flash(f'تم حذف الصف "{c.name}" بنجاح.', "info")
    return redirect(url_for("manage_assignments"))


@app.route("/admin/delete_subject/<int:subject_id>", methods=["POST"])
@app.route("/admin/subjects/delete/<int:subject_id>", methods=["POST"])
@admin_required
def delete_subject(subject_id):
    s = Subject.query.get_or_404(subject_id)
    db.session.delete(s)
    db.session.commit()
    flash(f'تم حذف المادة "{s.name}" بنجاح.', "info")
    return redirect(url_for("manage_assignments"))


@app.route("/admin/delete_assignment/<int:assignment_id>", methods=["POST"])
@app.route("/admin/assignments/delete/<int:assignment_id>", methods=["POST"])
@admin_required
def delete_assignment(assignment_id):
    a = TeacherAssignment.query.get_or_404(assignment_id)
    db.session.delete(a)
    db.session.commit()
    flash("تم إلغاء الإسناد بنجاح.", "info")
    return redirect(url_for("manage_assignments"))


@app.route("/admin/teachers", methods=["GET", "POST"])
@admin_required
def manage_teachers():
    if request.method == "POST":
        name = request.form.get("name")
        username = request.form.get("username")
        password = request.form.get("password")

        if User.query.filter_by(username=username).first():
            flash("اسم المستخدم موجود بالفعل! اختر اسماً آخر.", "danger")
        else:
            new_teacher = User(name=name, username=username, role="teacher")
            new_teacher.set_password(password)
            db.session.add(new_teacher)
            db.session.commit()
            flash(f"تم إضافة المعلم ({name}) بنجاح!", "success")
            return redirect(url_for("manage_teachers"))

    teachers = User.query.filter_by(role="teacher").all()
    return render_template("manage_teachers.html", teachers=teachers)


@app.route("/admin/teachers/delete/<int:teacher_id>", methods=["POST"])
@admin_required
def delete_teacher(teacher_id):
    teacher = User.query.get_or_404(teacher_id)
    if teacher.role == "teacher":
        db.session.delete(teacher)
        db.session.commit()
        flash("تم حذف المعلم وجميع ارتباطاته بنجاح.", "info")
    return redirect(url_for("manage_teachers"))


@app.route("/admin/teachers/reset_password/<int:teacher_id>", methods=["POST"])
@admin_required
def reset_teacher_password(teacher_id):
    teacher = User.query.get_or_404(teacher_id)
    new_password = request.form.get("new_password")

    if new_password and teacher.role == "teacher":
        teacher.set_password(new_password)
        db.session.commit()
        flash(
            f"تم إعادة تعيين كلمة المرور للمعلم ({teacher.name}) بنجاح!",
            "success",
        )
    else:
        flash("حدث خطأ أثناء تغيير كلمة المرور.", "danger")

    return redirect(url_for("manage_teachers"))


@app.route("/students_manage", methods=["GET", "POST"])
@admin_required
def students_manage():
    if request.method == "POST":
        name = request.form.get("name")
        grade_class = request.form.get("grade_class")
        parent_telegram_id = request.form.get("parent_telegram_id")
        parent_telegram_username = request.form.get("parent_telegram_username")

        # ربط الصف بجدول الصفوف واستخراج class_id
        school_class_obj = SchoolClass.query.filter_by(name=grade_class).first()
        if not school_class_obj:
            school_class_obj = SchoolClass(name=grade_class)
            db.session.add(school_class_obj)
            db.session.commit()

        # التعامل مع جدول ولي الأمر الموحد
        parent_obj = None
        if parent_telegram_id:
            parent_obj = Parent.query.filter_by(telegram_id=parent_telegram_id).first()
            if not parent_obj:
                parent_obj = Parent(
                    telegram_id=parent_telegram_id,
                    telegram_username=parent_telegram_username,
                    name=f"ولي أمر {name}"
                )
                db.session.add(parent_obj)
                db.session.commit()

        auto_code = generate_student_code()

        new_student = Student(
            student_code=auto_code,
            name=name,
            grade_class=grade_class,
            class_id=school_class_obj.id,
            parent_id=parent_obj.id if parent_obj else None,
            parent_telegram_id=parent_telegram_id if parent_telegram_id else None,
            parent_telegram_username=parent_telegram_username if parent_telegram_username else None,
        )
        db.session.add(new_student)

        qr_dir = os.path.join(app.static_folder or "static", "qrcodes")
        os.makedirs(qr_dir, exist_ok=True)
        qr_img = qrcode.make(auto_code)
        qr_img.save(os.path.join(qr_dir, f"{auto_code}.png"))

        db.session.commit()
        flash(f"تمت إضافة الطالب بنجاح! الكود المولد: {auto_code}", "success")
        return redirect(url_for("students_manage"))

    student_classes = [
        s[0]
        for s in db.session.query(Student.grade_class).distinct().all()
        if s[0]
    ]
    db_classes = [c.name for c in SchoolClass.query.all()]
    all_classes = sorted(list(set(student_classes + db_classes)))

    students = Student.query.order_by(
        Student.grade_class.asc(), Student.name.asc()
    ).all()
    return render_template(
        "students.html", students=students, classes=all_classes
    )


@app.route("/admin/students/edit/<int:student_id>", methods=["POST"])
@admin_required
def edit_student(student_id):
    student = Student.query.get_or_404(student_id)

    old_grade = student.grade_class
    new_name = request.form.get("name")
    new_grade = request.form.get("grade_class")
    telegram_id = request.form.get("parent_telegram_id")
    telegram_username = request.form.get("parent_telegram_username")

    student.name = new_name
    student.grade_class = new_grade

    # تحديث الصف وربطه بـ class_id
    if new_grade:
        school_class_obj = SchoolClass.query.filter_by(name=new_grade).first()
        if not school_class_obj:
            school_class_obj = SchoolClass(name=new_grade)
            db.session.add(school_class_obj)
            db.session.commit()
        student.class_id = school_class_obj.id

    student.parent_telegram_id = telegram_id if telegram_id else None
    student.parent_telegram_username = telegram_username if telegram_username else None

    # تحديث جدول ولي الأمر الموحد
    if telegram_id:
        parent_obj = Parent.query.filter_by(telegram_id=telegram_id).first()
        if not parent_obj:
            parent_obj = Parent(telegram_id=telegram_id, telegram_username=telegram_username,
                                name=f"ولي أمر {new_name}")
            db.session.add(parent_obj)
            db.session.commit()
        student.parent_id = parent_obj.id

    db.session.commit()

    target_chat_id = student.parent_telegram_id or (student.parent.telegram_id if student.parent else None)
    if old_grade != new_grade and target_chat_id:
        msg_text = (
            f"⚠️ *تنبيه من إدارة المدرسة*\n\n"
            f"عزيزي ولي الأمر، نود إعلامكم بأنه تم نقل الطالب/ة: *{student.name}*\n"
            f"من: *{old_grade}*\nإلى: *{new_grade}*\n\n"
            f"مع تحيات إدارة المدرسة."
        )
        send_telegram_msg(target_chat_id, msg_text)

    flash(f"تم تعديل بيانات الطالب {student.name} بنجاح!", "success")
    return redirect(url_for("students_manage"))


@app.route("/admin/students/delete/<int:student_id>", methods=["POST"])
@admin_required
def delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    student_name = student.name

    qr_file = os.path.join(
        app.static_folder or "static", "qrcodes", f"{student.student_code}.png"
    )
    if os.path.exists(qr_file):
        try:
            os.remove(qr_file)
        except Exception:
            pass

    db.session.delete(student)
    db.session.commit()
    flash(f"تم حذف الطالب ({student_name}) بشكل نهائي من المدرسة.", "info")
    return redirect(url_for("students_manage"))


@app.route("/admin/toggle_gate_lock", methods=["POST"])
@admin_required
def toggle_gate_lock():
    teacher_id = request.form.get("teacher_id")
    duration_minutes = request.form.get("duration_minutes", type=int, default=15)

    if not teacher_id:
        flash("⚠️ يرجى اختيار المعلم من القائمة المنسدلة!", "warning")
        return redirect(request.referrer or url_for("admin_dashboard"))

    teacher = User.query.get(teacher_id)
    if not teacher or teacher.role != "teacher":
        flash("⚠️ المعلم المحدد غير موجود!", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))

    unlock_until = datetime.now() + timedelta(minutes=duration_minutes)
    teacher.gate_scan_unlock_until = unlock_until
    db.session.commit()

    time_str = unlock_until.strftime("%I:%M %p")
    flash(
        f"🔓 تم فتح إمكانية مسح البوابة للمعلم ({teacher.name}) لمدة {duration_minutes} دقيقة (حتى الساعة {time_str}).",
        "success",
    )

    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/admin/sync_telegram_webhook", methods=["POST", "GET"])
@admin_required
def sync_telegram_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    try:
        response = requests.get(url, timeout=5)
        res_data = response.json()
        if res_data.get("ok"):
            bot_name = res_data["result"]["first_name"]
            username = res_data["result"]["username"]
            flash(
                f"✅ تم الاتصال ببوت التليجرام بنجاح! اسم البوت: {bot_name} (@{username})",
                "success",
            )
        else:
            flash(
                "⚠️ فشل الاتصال ببوت التليجرام، يرجى التأكد من التوكن (Bot Token).",
                "danger",
            )
    except Exception as e:
        flash(f"❌ حدث خطأ أثناء الاتصال بالتليجرام: {str(e)}", "danger")

    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/admin/set_gate_cutoff_time", methods=["POST"])
@admin_required
def set_gate_cutoff_time():
    cutoff_time = request.form.get("gate_cutoff_time")
    if cutoff_time:
        settings = SystemSettings.query.first()
        if not settings:
            settings = SystemSettings()
            db.session.add(settings)

        settings.gate_cutoff_time = cutoff_time
        db.session.commit()
        flash(
            f"🔒 تم تحديث وقت القفل التلقائي لشاشة البوابة إلى ({cutoff_time}) بنجاح.",
            "success",
        )
    return redirect(request.referrer or url_for("admin_dashboard"))


# =========================================================================
# 7. مسارات بوابة المعلم وقراءة الـ QR (Teacher Portal Routes)
# =========================================================================


@app.route("/teacher")
@login_required
def teacher_portal():
    teacher_id = current_user.id
    today = date.today()
    now = datetime.now()

    settings = SystemSettings.query.first()
    cutoff_str = (
        settings.gate_cutoff_time
        if (settings and settings.gate_cutoff_time)
        else "08:30"
    )

    try:
        cutoff_time = datetime.strptime(cutoff_str, "%H:%M").time()
    except ValueError:
        cutoff_time = datetime.strptime("08:30", "%H:%M").time()

    is_before_cutoff = now.time() < cutoff_time
    is_temporarily_unlocked = (
            getattr(current_user, "gate_scan_unlock_until", None) is not None
            and current_user.gate_scan_unlock_until > now
    )

    show_gate_scan = is_before_cutoff or is_temporarily_unlocked

    my_assignments = TeacherAssignment.query.filter_by(
        teacher_id=teacher_id
    ).all()

    temp_assigns = TemporaryAssignment.query.filter_by(
        substitute_teacher_id=teacher_id, date=today
    ).all()

    temp_assignment_ids = [t.assignment_id for t in temp_assigns]
    temp_teacher_assignments = (
        TeacherAssignment.query.filter(
            TeacherAssignment.id.in_(temp_assignment_ids)
        ).all()
        if temp_assignment_ids
        else []
    )

    all_active_assignments = list(
        set(my_assignments + temp_teacher_assignments)
    )

    selected_class_id = request.args.get("class_id", type=int)
    selected_subject_id = request.args.get("subject_id", type=int)

    students = []
    selected_class = None
    selected_subject = None

    if selected_class_id:
        selected_class = SchoolClass.query.get(selected_class_id)
        if selected_class:
            students = (
                Student.query.filter_by(grade_class=selected_class.name)
                .order_by(Student.name.asc())
                .all()
            )

    if selected_subject_id:
        selected_subject = Subject.query.get(selected_subject_id)

    return render_template(
        "teacher_portal.html",
        assignments=all_active_assignments,
        students=students,
        selected_class=selected_class,
        selected_subject=selected_subject,
        show_gate_scan=show_gate_scan,
    )


@app.route("/teacher/scan_gate", methods=["POST"])
@login_required
def teacher_scan_gate():
    student_code = request.form.get("student_code")
    today = date.today()
    now = datetime.now()

    settings = SystemSettings.query.first()
    cutoff_str = (
        settings.gate_cutoff_time
        if (settings and settings.gate_cutoff_time)
        else "08:30"
    )

    try:
        cutoff_time = datetime.strptime(cutoff_str, "%H:%M").time()
    except ValueError:
        cutoff_time = datetime.strptime("08:30", "%H:%M").time()

    is_before_cutoff = now.time() < cutoff_time
    is_temporarily_unlocked = (
            getattr(current_user, "gate_scan_unlock_until", None) is not None
            and current_user.gate_scan_unlock_until > now
    )

    if not (is_before_cutoff or is_temporarily_unlocked):
        flash(
            "🔒 مسح البوابة مقفل حالياً لحسابك. يرجى طلب فتح الصلاحية من إدارة المدرسة.",
            "danger",
        )
        return redirect(request.referrer or "/teacher")

    student = Student.query.filter_by(student_code=student_code).first()
    if not student:
        flash("كود الطالب غير صحيح أو غير موجود!", "danger")
        return redirect(request.referrer or "/teacher")

    already_scanned = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.type == "gate",
        db.func.date(Attendance.timestamp) == today,
    ).first()

    if already_scanned:
        flash(
            f"⚠️ الطالب ({student.name}) مسجل حضوره بالفعل اليوم عند البوابة!",
            "warning",
        )
    else:
        new_record = Attendance(
            student_id=student.id, type="gate", timestamp=now
        )
        db.session.add(new_record)
        db.session.commit()

        target_chat_id = student.parent_telegram_id or (student.parent.telegram_id if student.parent else None)
        if target_chat_id:
            msg = (
                f"🔔 *إشعار دخول المدرسة*\n\n"
                f"👤 *الطالب:* {student.name}\n"
                f"🏫 *الصف:* {student.grade_class}\n"
                f"⏰ *الوقت:* {now.strftime('%I:%M %p')}\n\n"
                f"✅ تم تسجيل الحضور بواسطة المعلم ({current_user.name})."
            )
            send_telegram_msg(target_chat_id, msg)

        flash(f"✅ تم تسجيل حضور الطالب ({student.name}) بنجاح.", "success")

    return redirect(request.referrer or "/teacher")


@app.route("/teacher/mark_attendance", methods=["POST"])
@login_required
def mark_attendance():
    student_code = request.form.get("student_code")
    class_name = request.form.get("class_name")
    subject_name = request.form.get("subject_name")
    today = date.today()

    student = Student.query.filter_by(student_code=student_code).first()
    if not student:
        flash("الطالب غير موجود!", "danger")
        return redirect(request.referrer or "/teacher")

    gate_scanned = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.type == "gate",
        db.func.date(Attendance.timestamp) == today,
    ).first()

    teacher_gate_scanned = ClassAttendance.query.filter(
        ClassAttendance.student_id == student.id,
        ClassAttendance.type == "gate",
        db.func.date(ClassAttendance.timestamp) == today,
    ).first()

    if not gate_scanned and not teacher_gate_scanned:
        flash(
            f"🛑 تعذر تسجيل الحضور! الطالب ({student.name}) لم يسجل دخول عند بوابة المدرسة اليوم. يجب مراجعة الإدارة أولاً.",
            "danger",
        )
        return redirect(request.referrer or "/teacher")

    already_marked = ClassAttendance.query.filter(
        ClassAttendance.student_id == student.id,
        ClassAttendance.class_name == class_name,
        ClassAttendance.subject_name == subject_name,
        ClassAttendance.type == "class_attendance",
        db.func.date(ClassAttendance.timestamp) == today,
    ).first()

    if already_marked:
        time_scanned = already_marked.timestamp.strftime("%I:%M %p")
        flash(
            f"⚠️ الطالب ({student.name}) مسجل حضوره بالفعل في هذه الحصة الساعة {time_scanned}!",
            "warning",
        )
    else:
        now = datetime.now()
        time_str = now.strftime("%I:%M %p")

        attendance = ClassAttendance(
            student_id=student.id,
            teacher_id=current_user.id,
            subject_name=subject_name,
            class_name=class_name,
            type="class_attendance",
            points_change=0,
            note=f"حاضر في الحصة (وقت التسجيل: {time_str})",
        )
        db.session.add(attendance)
        db.session.commit()

        flash(
            f"✅ تم تسجيل حضور الطالب ({student.name}) في الحصة بنجاح الساعة {time_str}",
            "success",
        )

    return redirect(request.referrer or "/teacher")


@app.route("/teacher/save_behavior", methods=["POST"])
@login_required
def save_behavior():
    student_code = request.form.get("student_code")
    class_name = request.form.get("class_name")
    subject_name = request.form.get("subject_name")
    points = request.form.get("points", type=int, default=0)
    note = request.form.get("note", default="")

    student = Student.query.filter_by(student_code=student_code).first()
    if not student:
        flash("الطالب غير موجود!", "danger")
        return redirect(request.referrer or "/teacher")

    student.points += points

    behavior_record = ClassAttendance(
        student_id=student.id,
        teacher_id=current_user.id,
        subject_name=subject_name,
        class_name=class_name,
        type="behavior_eval",
        points_change=points,
        note=note or "تقييم سلوك أثناء الحصة",
    )
    db.session.add(behavior_record)
    db.session.commit()

    flash(
        f"⭐ تم حفظ تقييم السلوك للطالب ({student.name}) | النقاط: {points}",
        "info",
    )
    return redirect(request.referrer or "/teacher")


@app.route("/gate")
def gate_page():
    return render_template("gate_scan.html")


# =========================================================================
# 8. واجهات البرمجة المفتوحة (API Endpoints)
# =========================================================================


@app.route("/api/scan_gate", methods=["POST"])
def scan_gate():
    code = request.json.get("code")
    student = Student.query.filter_by(student_code=code).first()

    if not student:
        return jsonify({"status": "error", "message": "رمز الطالب غير صحيح!"}), 404

    now = datetime.now()
    today = date.today()

    recent_scan = (
        Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.type == "gate",
            db.func.date(Attendance.timestamp) == today,
        )
        .order_by(Attendance.timestamp.desc())
        .first()
    )

    if recent_scan and (now - recent_scan.timestamp).total_seconds() < 300:
        return jsonify(
            {
                "status": "warning",
                "message": f"تم تسجيل دخول {student.name} مسبقاً!",
            }
        )

    att = Attendance(student_id=student.id, type="gate", timestamp=now)
    db.session.add(att)
    db.session.commit()

    target_chat_id = student.parent_telegram_id or (student.parent.telegram_id if student.parent else None)
    if target_chat_id:
        msg = (
            f"🔔 *إشعار دخول المدرسة*\n\n"
            f"👤 *الطالب:* {student.name}\n"
            f"🏫 *الصف:* {student.grade_class}\n"
            f"📅 *التاريخ:* {now.strftime('%Y-%m-%d')}\n"
            f"⏰ *وقت الدخول:* {now.strftime('%I:%M %p')}\n\n"
            f"✅ تم تسجيل دخول الطالب بنجاح."
        )
        send_telegram_msg(target_chat_id, msg)

    return jsonify(
        {
            "status": "success",
            "student_name": student.name,
            "grade_class": student.grade_class,
            "time": now.strftime("%I:%M %p"),
            "points": student.points,
        }
    )


@app.route("/api/run_audit_now", methods=["POST"])
@login_required
def run_audit_now():
    if current_user.role != "admin":
        return jsonify(
            {"status": "error", "message": "غير مصرح لك بهذا الإجراء."}
        ), 403

    audit_daily_attendance()
    return jsonify(
        {
            "status": "success",
            "message": "تم إجراء الفحص الشامل للطلاب وتنبيه الأولياء بنجاح.",
        }
    )


# =========================================================================
# 9. التدقيق التلقائي وجدولة المهام (Background Scheduler)
# =========================================================================
def audit_daily_attendance():
    with app.app_context():
        today = date.today()
        settings = SystemSettings.query.first()

        # جلب الإعدادات (مع وضع قيم افتراضية في حال عدم وجودها)
        target_lessons = settings.daily_actual_lessons if settings else 6
        periods_limit = settings.periods_per_absent_day if settings else 5  # عدد حصص التسرب التي تعادل يوم غياب
        days_limit = settings.max_absent_days_warning if settings else 15

        # 1. جمع معرفات الطلاب الحاضرين عند البوابة اليوم
        gate_records = Attendance.query.filter(
            Attendance.type == "gate",
            db.func.date(Attendance.timestamp) == today,
        ).all()

        teacher_gate_records = ClassAttendance.query.filter(
            ClassAttendance.type == "gate",
            db.func.date(ClassAttendance.timestamp) == today,
        ).all()

        present_student_ids = set([r.student_id for r in gate_records] + [tr.student_id for tr in teacher_gate_records])

        # 2. فحص الطلاب المتسربين أو الذين حضروا البوابة ولم يحضروا أي حصة
        for student_id in present_student_ids:
            student = Student.query.get(student_id)
            if not student:
                continue

            attended_classes_count = ClassAttendance.query.filter(
                ClassAttendance.student_id == student.id,
                ClassAttendance.type == "class_attendance",
                db.func.date(ClassAttendance.timestamp) == today,
            ).count()

            # --- حالة الغياب التام: سجل عند البوابة ولم يحضر أي حصة ---
            if attended_classes_count == 0:
                already_absent_audited = BehaviorLog.query.filter(
                    BehaviorLog.student_id == student.id,
                    BehaviorLog.reason.like("%غياب تام%"),
                    db.func.date(BehaviorLog.timestamp) == today,
                ).first()

                if already_absent_audited:
                    continue

                # زيادة أيام الغياب بمقدار يوم واحد
                student.absent_days_count += 1

                # فحص حد الإنذار
                if student.absent_days_count >= days_limit and not student.warning_sent:
                    send_parent_warning_notification(student)
                    student.warning_sent = True

                log = BehaviorLog(
                    student_id=student.id,
                    points_change=0,
                    reason=f"غياب تام: تسجيل دخول عند البوابة وعدم حضور أي حصة اليوم",
                    teacher_name="النظام الآلي",
                    timestamp=datetime.now(),
                )
                db.session.add(log)

                absence_record = Attendance(
                    student_id=student.id,
                    type="absence",
                    timestamp=datetime.now()
                )
                db.session.add(absence_record)

                # 💡 إرسال إشعار تليجرام لولي الأمر بالغياب التام
                target_chat_id = student.parent_telegram_id or (student.parent.telegram_id if student.parent else None)
                if target_chat_id:
                    absent_msg = (
                        f"🚨 *تنبيه غياب تام عن الحصص*\n\n"
                        f"👤 *الطالب:* {student.name}\n"
                        f"🏫 *الصف:* {student.grade_class}\n"
                        f"📅 *التاريخ:* {today.strftime('%Y-%m-%d')}\n\n"
                        f"⚠️ *تفاصيل:* سجل الطالب دخولاً عند البوابة اليوم، ولكنه لم يحضر أي حصة دراسية.\n"
                        f"📊 *إجمالي أيام الغياب:* {student.absent_days_count} يوم."
                    )
                    send_telegram_msg(target_chat_id, absent_msg)

                continue

            # --- حالة التسرب الجزئي: حضر بعض الحصص وتغيب عن الباقي ---
            already_audited = BehaviorLog.query.filter(
                BehaviorLog.student_id == student.id,
                BehaviorLog.reason.like("%تسرب سلوكي%"),
                db.func.date(BehaviorLog.timestamp) == today,
            ).first()

            if already_audited:
                continue

            if attended_classes_count < target_lessons:
                student.points -= 10
                missing_classes = target_lessons - attended_classes_count

                # إضافة الحصص المفقودة إلى عمود التسرب
                student.absent_periods_count += missing_classes

                # إذا تجاوز عدد حصص التسرب الحد المسموح يتم تحويلها ليوم غياب وتصفير/تعديل العداد
                if student.absent_periods_count >= periods_limit:
                    new_days = student.absent_periods_count // periods_limit
                    student.absent_days_count += new_days
                    student.absent_periods_count = student.absent_periods_count % periods_limit

                # فحص حد الإنذار بعد التحويل
                if student.absent_days_count >= days_limit and not student.warning_sent:
                    send_parent_warning_notification(student)
                    student.warning_sent = True

                log = BehaviorLog(
                    student_id=student.id,
                    points_change=-10,
                    reason=f"تسرب سلوكي: عدم حضور كافة الحصص ({attended_classes_count}/{target_lessons})",
                    teacher_name="النظام الآلي",
                    timestamp=datetime.now(),
                )
                db.session.add(log)

                target_chat_id = student.parent_telegram_id or (student.parent.telegram_id if student.parent else None)
                if target_chat_id:
                    alert_msg = (
                        f"🚨 *تنبيه تسرب وعدم اكتمال الحصص*\n\n"
                        f"👤 *الطالب:* {student.name}\n"
                        f"🏫 *الصف:* {student.grade_class}\n"
                        f"📅 *التاريخ:* {today.strftime('%Y-%m-%d')}\n\n"
                        f"⚠️ *تفاصيل:* سجل الطالب دخولاً عند البوابة، ولكنه حضر ({attended_classes_count}) حصص فقط من أصل ({target_lessons}) حصص اليوم.\n"
                        f"🔻 *الخصم:* تم خصم 10 نقاط من رصيده.\n"
                        f"📊 *الرصيد الحالي:* {student.points} نقطة."
                    )
                    send_telegram_msg(target_chat_id, alert_msg)

        # 3. فحص وتسجيل غياب الطلاب الذين لم يسجلوا دخولاً على البوابة نهائياً اليوم (غياب تام)
        all_students = Student.query.all()
        for student in all_students:
            if student.id not in present_student_ids:
                already_absent_audited = BehaviorLog.query.filter(
                    BehaviorLog.student_id == student.id,
                    BehaviorLog.reason.like("%غياب تام%"),
                    db.func.date(BehaviorLog.timestamp) == today,
                ).first()

                if already_absent_audited:
                    continue

                # زيادة أيام الغياب بمقدار يوم واحد
                student.absent_days_count += 1

                # فحص حد الإنذار
                if student.absent_days_count >= days_limit and not student.warning_sent:
                    send_parent_warning_notification(student)
                    student.warning_sent = True

                log = BehaviorLog(
                    student_id=student.id,
                    points_change=0,
                    reason=f"غياب تام: عدم تسجيل دخول عند بوابة المدرسة اليوم",
                    teacher_name="النظام الآلي",
                    timestamp=datetime.now(),
                )
                db.session.add(log)

                absence_record = Attendance(
                    student_id=student.id,
                    type="absence",
                    timestamp=datetime.now()
                )
                db.session.add(absence_record)

                # 💡 إرسال إشعار تليجرام لولي الأمر للغياب عن المدرسة نهائياً
                target_chat_id = student.parent_telegram_id or (student.parent.telegram_id if student.parent else None)
                if target_chat_id:
                    gate_absent_msg = (
                        f"❌ *تنبيه غياب عن الدوام المدرسي*\n\n"
                        f"👤 *الطالب:* {student.name}\n"
                        f"🏫 *الصف:* {student.grade_class}\n"
                        f"📅 *التاريخ:* {today.strftime('%Y-%m-%d')}\n\n"
                        f"⚠️ *تفاصيل:* لم يتم تسجيل أي حضور للطالب عند بوابة المدرسة اليوم.\n"
                        f"📊 *إجمالي أيام الغياب:* {student.absent_days_count} يوم."
                    )
                    send_telegram_msg(target_chat_id, gate_absent_msg)

        User.query.filter(User.role == "teacher").update(
            {User.gate_scan_unlock_until: None}
        )

        db.session.commit()


# =========================================================================
# 10. التقارير والطباعة (Reports Routes)
# =========================================================================


@app.route("/reports/daily_pdf")
@login_required
def export_daily_pdf():
    today = date.today()
    settings = SystemSettings.query.first()
    TOTAL_LESSONS = settings.daily_actual_lessons if settings else 6

    all_students = Student.query.order_by(
        Student.grade_class.asc(), Student.name.asc()
    ).all()
    gate_attendance_ids = [
        att.student_id
        for att in Attendance.query.filter(
            Attendance.type == "gate",
            db.func.date(Attendance.timestamp) == today,
        ).all()
    ]

    absent_students = []
    leakers_students = []

    for student in all_students:
        if student.id not in gate_attendance_ids:
            absent_students.append(student)
        else:
            class_count = ClassAttendance.query.filter(
                ClassAttendance.student_id == student.id,
                ClassAttendance.type == "class_attendance",
                db.func.date(ClassAttendance.timestamp) == today,
            ).count()

            if class_count < TOTAL_LESSONS:
                leakers_students.append(
                    {
                        "student": student,
                        "attended_classes": class_count,
                        "missing_classes": TOTAL_LESSONS - class_count,
                    }
                )

    return render_template(
        "daily_report_pdf.html",
        today=today.strftime("%Y-%m-%d"),
        absent_students=absent_students,
        leakers_students=leakers_students,
        total_absent=len(absent_students),
        total_leakers=len(leakers_students),
    )


@app.route("/reports/student/<int:student_id>")
@login_required
def student_individual_report(student_id):
    student = Student.query.get_or_404(student_id)

    class_attendances = (
        ClassAttendance.query.filter_by(student_id=student.id)
        .order_by(ClassAttendance.timestamp.desc())
        .all()
    )

    gate_attendances = (
        Attendance.query.filter_by(student_id=student.id, type="gate")
        .order_by(Attendance.timestamp.desc())
        .all()
    )

    behavior_records = (
        BehaviorLog.query.filter_by(student_id=student.id)
        .order_by(BehaviorLog.timestamp.desc())
        .all()
    )

    gate_visits_count = len(gate_attendances)
    classes_attended_count = ClassAttendance.query.filter_by(
        student_id=student.id, type="class_attendance"
    ).count()

    return render_template(
        "student_report_pdf.html",
        student=student,
        class_attendances=class_attendances,
        gate_attendances=gate_attendances,
        behaviors=behavior_records,
        gate_visits=gate_visits_count,
        classes_attended=classes_attended_count,
    )


# =========================================================================
# 11. التشغيل وتجهيز البيانات الأولية (Application Entry Point)
# =========================================================================

with app.app_context():
    db.create_all()

    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", name="مدير النظام", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)

    if not SystemSettings.query.first():
        default_settings = SystemSettings(
            periods_per_absent_day=7,
            max_absent_days_warning=15,
            daily_actual_lessons=6,
        )
        db.session.add(default_settings)

    db.session.commit()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

