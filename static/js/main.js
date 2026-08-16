/**
 * main.js
 * ملف الجافاسكريبت المجمع والمنظم للنظام
 */

/* =========================================
   1. تهيئة النظام (عند تحميل الصفحة)
   ========================================= */
document.addEventListener('DOMContentLoaded', function () {

    // أ. إخفاء رسائل التنبيه (Flash Messages) تلقائياً بعد 4 ثوانٍ
    setTimeout(function() {
        const alerts = document.querySelectorAll('.alert');
        alerts.forEach(function(alert) {
            alert.style.transition = "opacity 0.6s ease, transform 0.6s ease";
            alert.style.opacity = "0";
            alert.style.transform = "translateY(-10px)";
            setTimeout(function() { alert.remove(); }, 600);
        });
    }, 4000);

    // ب. تهيئة البحث المباشر في الجدول (Vanilla JS)
    const searchInput = document.getElementById('studentSearch');
    if (searchInput) {
        searchInput.addEventListener('keyup', function () {
            const filter = this.value.toLowerCase();
            const rows = document.querySelectorAll('#studentsTable tbody tr');
            rows.forEach(row => {
                row.style.display = row.textContent.toLowerCase().includes(filter) ? '' : 'none';
            });
        });
    }

    // ج. تأكيد الإرسال الجماعي للغياب
    const notifyAllForm = document.getElementById('notifyAllAbsentForm');
    if (notifyAllForm) {
        notifyAllForm.addEventListener('submit', function (e) {
            if (!confirm('هل تريد إرسال تنبيهات غياب لكافة أولياء الأمور للطلاب الغائبين؟')) {
                e.preventDefault();
            }
        });
    }

    // د. تأكيد الحذف للمعلمين
    const deleteForms = document.querySelectorAll('.delete-teacher-form');
    deleteForms.forEach(form => {
        form.addEventListener('submit', function (e) {
            if (!confirm('هل أنت متأكد من حذف هذا المعلم؟')) {
                e.preventDefault();
            }
        });
    });

    // هـ. تهيئة قارئ QR الخاص بالبوابة (API)
    if (document.getElementById('reader')) {
        let html5QrcodeScanner = new Html5QrcodeScanner("reader", { fps: 10, qrbox: { width: 250, height: 250 } }, false);
        html5QrcodeScanner.render(onScanSuccessAPI);
    }
});

/* =========================================
   2. وظائف البحث والفلترة (Search & Filter)
   ========================================= */

// البحث العام عن الطلاب
function filterStudents() {
    const input = document.getElementById("searchInput");
    if (!input) return;
    const filter = input.value.toLowerCase();
    const rows = document.querySelectorAll("#studentsTable tbody .student-row");
    rows.forEach(function(row) {
        row.style.display = row.innerText.toLowerCase().includes(filter) ? "" : "none";
    });
}

// تصفية الفروع في نافذة التعديل
function filterBranchesForStudent(studentId, currentGradeClass) {
    const selectElem = document.getElementById('select_grade_' + studentId);
    if (!selectElem) return;
    const baseGrade = currentGradeClass.split(' ')[0].trim();
    Array.from(selectElem.options).forEach(option => {
        option.style.display = option.value.startsWith(baseGrade) ? 'block' : 'none';
    });
}

/* =========================================
   3. وظائف التحديد والإجراءات (Actions)
   ========================================= */

// تحديد كافة الطلاب
function toggleSelectAll(master) {
    const checkboxes = document.querySelectorAll('.student-checkbox');
    checkboxes.forEach(cb => {
        if (cb.closest('tr').style.display !== 'none') {
            cb.checked = master.checked;
        }
    });
    updateSelectedCount();
}

// تحديث عداد الطلاب المحددين
function updateSelectedCount() {
    const selected = document.querySelectorAll('.student-checkbox:checked').length;
    const countElem = document.getElementById('selectedCount');
    if (countElem) countElem.innerText = selected;
}

// إجراء فحص التسرب (Audit)
function triggerAudit() {
    if (confirm("هل تريد إجراء فحص شامل للتسرب وإرسال تنبيهات فورية لأولياء الأمور الآن؟")) {
        fetch('/api/run_audit_now', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
        .then(res => res.json())
        .then(data => {
            alert(data.message);
            location.reload();
        })
        .catch(err => alert("حدث خطأ أثناء الاتصال بالسيرفر!"));
    }
}

// تنفيذ نموذج حذف ديناميكي
function submitDeleteForm(selectId, deleteUrlPrefix) {
    const select = document.getElementById(selectId);
    if (!select || !select.value) {
        alert('يرجى تحديد عنصر من القائمة المنسدلة أولاً.');
        return;
    }
    if (confirm('هل أنت متأكد من إجراء عملية الحذف هذه؟')) {
        const form = document.getElementById('dynamicDeleteForm');
        if (form) {
            form.action = deleteUrlPrefix + select.value;
            form.submit();
        }
    }
}

/* =========================================
   4. وظائف الطباعة (Printing)
   ========================================= */

function printReport() {
    window.print();
}

function printSingleQR(name, gradeClass, code, qrImgUrl) {
    // استخدام رابط الـ API المباشر بناءً على كود الطالب لضمان عمل الطباعة دائماً
    let dynamicQrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=${code}`;
    printCards([{ name, gradeClass, code, qrImgUrl: dynamicQrUrl }]);
}

function printSelectedQRs() {
    const selectedBoxes = document.querySelectorAll('.student-checkbox:checked');
    if (selectedBoxes.length === 0) {
        alert("يرجى تحديد طالب واحد على الأقل للطباعة!");
        return;
    }
    const cardsData = Array.from(selectedBoxes).map(cb => ({
        name: cb.getAttribute('data-name'),
        gradeClass: cb.getAttribute('data-grade'),
        code: cb.getAttribute('data-code'),
        qrImgUrl: cb.getAttribute('data-qr')
    }));
    printCards(cardsData);
}

// دالة إنشاء نافذة الطباعة وتوليد البطاقات
function printCards(cardsArray) {
    const printWindow = window.open('', '', 'height=700,width=800');
    const htmlContent = `
    <html dir="rtl"><head><title>طباعة بطاقات الطلاب</title>
    <style>
        @page { size: auto; margin: 10mm; }
        body { font-family: "Cairo", "Segoe UI", sans-serif; margin: 0; background-color: #fff; }
        .cards-wrapper { display: flex; flex-wrap: wrap; gap: 15px; justify-content: flex-start; }
        .id-card { width: 300px; padding: 20px; border: 2px solid #1a365d; border-radius: 16px; text-align: center; page-break-inside: avoid; }
        .header-title { font-size: 16px; font-weight: bold; color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-bottom: 15px; }
        .student-name { font-size: 18px; font-weight: bold; color: #0f172a; margin-bottom: 8px; }
        .grade-badge { display: inline-block; background-color: #f1f5f9; color: #1a365d; padding: 4px 12px; border-radius: 8px; font-size: 14px; font-weight: bold; margin-bottom: 15px; border: 1px solid #cbd5e1; }
        .qr-container { padding: 10px; border-radius: 12px; display: inline-block; border: 2px dashed #94a3b8; }
        .qr-container img { width: 130px; height: 130px; display: block; }
        .student-code { font-family: monospace; font-size: 16px; font-weight: bold; color: #1a365d; margin-top: 10px; }
    </style></head><body>
    <div class="cards-wrapper">
        ${cardsArray.map(card => `
            <div class="id-card">
                <div class="header-title">بطاقة المتابعة المدرسية</div>
                <div class="student-name">${card.name}</div>
                <div class="grade-badge">الصف: ${card.gradeClass}</div><br>
                <div class="qr-container"><img src="${card.qrImgUrl}" /></div>
                <div class="student-code">${card.code}</div>
            </div>
        `).join('')}
    </div></body></html>`;
    printWindow.document.write(htmlContent);
    printWindow.document.close();
    setTimeout(() => printWindow.print(), 500);
}

/* =========================================
   5. وظائف الكاميرا وقارئ QR (QR & Camera)
   ========================================= */

// دالة نجاح المسح للبوابة (API)
function onScanSuccessAPI(decodedText, decodedResult) {
    fetch('/api/scan_gate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: decodedText })
    })
    .then(res => res.json())
    .then(data => {
        const resDiv = document.getElementById('scan-result');
        if (!resDiv) return;
        resDiv.style.display = 'block';

        const nameEl = document.getElementById('student-name');
        const classEl = document.getElementById('student-class');
        const timeEl = document.getElementById('scan-time');
        const pointsEl = document.getElementById('student-points');

        if (data.status === 'success') {
            resDiv.className = 'mt-4 p-3 rounded bg-success-subtle text-success border border-success fw-bold';
            if (nameEl) nameEl.innerText = data.student_name;
            if (classEl) classEl.innerText = "الصف: " + data.grade_class;
            if (timeEl) timeEl.innerText = "وقت الدخول: " + data.time;
            if (pointsEl) pointsEl.innerText = data.points;
        } else {
            const state = data.status === 'warning' ? 'warning' : 'danger';
            resDiv.className = `mt-4 p-3 rounded bg-${state}-subtle text-${state} border border-${state} fw-bold`;
            if (nameEl) nameEl.innerText = data.message;
        }
    })
    .catch(err => console.error("Error:", err));
}

// المتغيرات الخاصة بكاميرا المعلم
let html5QrCodeForm = null;
let isCameraRunningForm = false;

// دالة نجاح المسح للكاميرا اليدوية
function onScanSuccessForm(decodedText) {
    const inputElem = document.getElementById('student_code_input');
    if (inputElem) inputElem.value = decodedText;
    const formElem = document.getElementById('gate-scan-form');
    if (html5QrCodeForm && isCameraRunningForm) {
        html5QrCodeForm.stop().then(() => { if (formElem) formElem.submit(); }).catch(() => { if (formElem) formElem.submit(); });
    } else {
        if (formElem) formElem.submit();
    }
}

function startCamera() {
    const qrContainer = document.getElementById('qr-reader');
    if (!qrContainer) return;
    qrContainer.style.display = 'block';
    if (!html5QrCodeForm) html5QrCodeForm = new Html5Qrcode("qr-scanner-view");
    html5QrCodeForm.start(
        { facingMode: "environment" },
        { fps: 10, qrbox: { width: 220, height: 220 } },
        onScanSuccessForm
    ).then(() => {
        isCameraRunningForm = true;
        const btnText = document.getElementById('camera-btn-text');
        if (btnText) btnText.innerText = "إغلاق الكاميرا";
    }).catch(err => { alert("تعذر الوصول للكاميرا."); qrContainer.style.display = 'none'; });
}

function stopCamera() {
    if (html5QrCodeForm && isCameraRunningForm) {
        html5QrCodeForm.stop().then(() => {
            isCameraRunningForm = false;
            const qrContainer = document.getElementById('qr-reader');
            if (qrContainer) qrContainer.style.display = 'none';
            const btnText = document.getElementById('camera-btn-text');
            if (btnText) btnText.innerText = "فتح الكاميرا";
        });
    }
}

function toggleCamera() {
    isCameraRunningForm ? stopCamera() : startCamera();
}

/* =========================================
   6. تعاملات jQuery (Dynamic AJAX)
   ========================================= */

// تصفية البحث باستخدام jQuery
$('#searchStudent').on('keyup', function() {
    var value = $(this).val().toLowerCase();
    $('#studentsTable tbody tr').filter(function() {
        $(this).toggle($(this).data('name').toLowerCase().indexOf(value) > -1);
    });
});

// تسجيل الحضور للحصة
$('.btn-log-attend').click(function() {
    var studentId = $(this).data('id');
    var className = $('#classNameInput').val() || 'حصة عادية';
    $.ajax({
        url: '/api/log_behavior',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ student_id: studentId, action_type: 'attendance_class', class_name: className }),
        success: function(res) { alert(res.message); }
    });
});

// تسجيل التقييم السلوكي
$('.btn-points').click(function() {
    var studentId = $(this).data('id');
    var pts = $(this).data('pts');
    var reason = $(this).data('reason');
    $.ajax({
        url: '/api/log_behavior',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ student_id: studentId, action_type: 'behavior', points: pts, reason: reason }),
        success: function(res) {
            if(res.status === 'success') {
                $('#points-' + studentId).text(res.new_points);
                alert(res.message);
            }
        }
    });
});