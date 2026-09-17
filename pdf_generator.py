"""
PDF Generation Module for Quiz Questions
🇮🇳 Generate high-quality PDFs of quiz questions with answers and explanations
"""

import sqlite3
import json
import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
import logging

DB_FILE = "quiz_bot.db"
PDF_OUTPUT_DIR = "quiz_pdfs"

# Create output directory if it doesn't exist
if not os.path.exists(PDF_OUTPUT_DIR):
    os.makedirs(PDF_OUTPUT_DIR)

def escape_special_chars(text):
    """Escape special characters for ReportLab"""
    if not text:
        return ""
    
    text = str(text)
    # Replace special characters that cause issues in ReportLab
    replacements = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&apos;'
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

def generate_quiz_pdf(quiz_id):
    """
    Quiz ID se database se saare sawaal nikaal kar clean PDF banata hai
    """
    try:
        # Fetch quiz details
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT title, description, timer, negative_value FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        
        if not quiz_data:
            logging.error(f"Quiz {quiz_id} not found")
            conn.close()
            return None
        
        title, description, timer, negative_value = quiz_data
        
        # 🟢 DATABASE SE US QUIZ KE SAARE (ALL) QUESTIONS NIKALE
        cursor.execute("""
            SELECT id, question_text, options, correct_answer, explanation, pre_message 
            FROM questions 
            WHERE quiz_id = ? 
            ORDER BY id ASC
        """, (quiz_id,))
        questions = cursor.fetchall()
        conn.close()
        
        if not questions:
            logging.warning(f"No questions found for quiz {quiz_id}")
            return None
        
        # Create PDF file setup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{PDF_OUTPUT_DIR}/quiz_{quiz_id}_{timestamp}.pdf"
        
        doc = SimpleDocTemplate(
            filename,
            pagesize=A4,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch
        )
        
        story = []
        styles = getSampleStyleSheet()
        
        # 🟢 CLEAN STYLES (No boxes, no borders, clear white background)
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            textColor=colors.HexColor('#1F77B4'), # Royal Blue Text
            spaceAfter=12,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        quiz_info_style = ParagraphStyle(
            'QuizInfo',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#444444'), # Dark Grey
            spaceAfter=6,
            alignment=TA_LEFT
        )
        
        question_style = ParagraphStyle(
            'Question',
            parent=styles['Heading2'],
            fontSize=12,
            textColor=colors.HexColor('#2E5090'), # Smooth Blue Text (No background box)
            spaceAfter=8,
            spaceBefore=12,
            fontName='Helvetica-Bold'
        )
        
        option_style = ParagraphStyle(
            'Option',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#333333'), # Plain Dark Text
            spaceAfter=4,
            leftIndent=20
        )
        
        answer_style = ParagraphStyle(
            'Answer',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#006400'), # Deep Green text for correct answer
            spaceAfter=6,
            fontName='Helvetica-Bold',
            leftIndent=20
        )
        
        explanation_style = ParagraphStyle(
            'Explanation',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#555555'), # Soft Grey
            spaceAfter=12,
            leftIndent=20
        )
        
        # ============ TITLE & METADATA ============
        story.append(Paragraph(escape_special_chars(title), title_style))
        story.append(Spacer(1, 0.1*inch))
        
        # Quiz metadata
        if description:
            story.append(Paragraph(f"<b>Description:</b> {escape_special_chars(description)}", quiz_info_style))
        
        story.append(Paragraph(f"<b>Total Questions:</b> {len(questions)}", quiz_info_style))
        story.append(Paragraph(f"<b>Time per Question:</b> {timer} seconds", quiz_info_style))
        story.append(Paragraph(f"<b>Negative Marking:</b> -{negative_value} per wrong answer", quiz_info_style))
        story.append(Paragraph(f"<b>Generated on:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", quiz_info_style))
        
        story.append(Spacer(1, 0.2*inch))
        story.append(Paragraph("_" * 80, quiz_info_style))
        story.append(Spacer(1, 0.2*inch))
        
        # ============ QUESTIONS & ANSWERS ============
        for idx, question in enumerate(questions, 1):
            q_id, q_text, options_json, correct_ans, explanation, pre_message = question
            options = json.loads(options_json)
            
            # Convert correct_ans to integer index (safety check)
            try:
                correct_idx = int(correct_ans)
                if correct_idx < 0 or correct_idx >= len(options):
                    correct_idx = 0
            except (ValueError, TypeError):
                try:
                    correct_idx = options.index(str(correct_ans))
                except ValueError:
                    correct_idx = 0
            
            # Question Header (Blue Text, No Box)
            question_text = f"<b>Q{idx}.</b> {escape_special_chars(q_text)}"
            story.append(Paragraph(question_text, question_style))
            
            # Pre-message if exists
            if pre_message:
                story.append(Paragraph(f"<b>Context:</b> {escape_special_chars(pre_message)}", quiz_info_style))
            
            story.append(Spacer(1, 0.05*inch))
            
            # Options (Normal Text list)
            for opt_idx, option in enumerate(options):
                is_correct = (opt_idx == correct_idx)
                
                if is_correct:
                    # Highlight correct option inline text with green color
                    option_text = f"<b>✓ Option {opt_idx + 1}:</b> <font color='green'><b>{escape_special_chars(option)}</b></font>"
                else:
                    option_text = f"<b>Option {opt_idx + 1}:</b> {escape_special_chars(option)}"
                
                story.append(Paragraph(option_text, option_style))
            
            story.append(Spacer(1, 0.08*inch))
            
            # Correct answer indicator (Green Text, No Box)
            answer_text = f"<b>✓ Correct Answer:</b> <font color='green'><b>Option {correct_idx + 1}: {escape_special_chars(options[correct_idx])}</b></font>"
            story.append(Paragraph(answer_text, answer_style))
            
            # Explanation (Grey Text, No Box or Borders)
            if explanation and str(explanation).strip():
                explanation_text = f"<b>📖 Explanation:</b> {escape_special_chars(explanation)}"
                story.append(Paragraph(explanation_text, explanation_style))
            
            story.append(Spacer(1, 0.15*inch))
            
            # Page break after every 3 questions
            if idx % 3 == 0 and idx < len(questions):
                story.append(PageBreak())
        
        # ============ BUILD PDF ============
        doc.build(story)
        
        logging.info(f"✅ PDF generated successfully: {filename}")
        return filename
        
    except Exception as e:
        logging.error(f"Error generating PDF for quiz {quiz_id}: {e}", exc_info=True)
        return None

def get_pdf_file_size(filepath):
    """Get PDF file size in KB"""
    try:
        size_bytes = os.path.getsize(filepath)
        return round(size_bytes / 1024, 2)  # Convert to KB
    except Exception:
        return 0
        
