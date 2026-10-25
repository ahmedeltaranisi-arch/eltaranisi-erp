PT Bold Heading — assets/fonts/PTBoldHeading.ttf
================================================
الخط المطلوب من العميل لطباعة التقارير (PDF) ولتصدير ملفات Excel.

* العائلة: PT Bold Heading  (PostScript: PTBoldHeading, Version 2.10)
* الحقوق: "Designed for Microsoft Corp. by Platinum Inc." — من حزمة الخطوط
  العربية التي وزّعتها مايكروسوفت، ويُستخدم هنا بطلب صريح من العميل.
* الملف: 68 KB، TrueType، يحتوي 117/117 من أشكال العرض العربية التي يحتاجها
  مولّد الـPDF (pdfgen.shape_arabic) + الأرقام العربية الهندية، ولا يحتوي
  الأرقام اللاتينية — لذلك تبقى الأرقام واللاتيني على DejaVu Sans داخل PDF.

كيف يُستبدل الخط:
    ERP_PDF_FONT=NotoSansArabic.ttf     # ملف PDF
    ERP_XLSX_FONT="PT Bold Heading"      # اسم الخط داخل ملفات Excel

ملفات الخطوط الأخرى في هذا المجلد (NotoSansArabic, Amiri, DejaVu) تبقى كخطوط
بديلة (fallback) حتى لا يتعطل أي تقرير إذا غاب الملف.
