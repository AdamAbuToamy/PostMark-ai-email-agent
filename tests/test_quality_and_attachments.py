import io
import unittest
from email_agent.infrastructure.attachment_text import extract_local


class AttachmentTests(unittest.TestCase):
    def test_unicode_html_is_local_sanitized_and_binary_files_are_rejected(self):
        self.assertEqual(extract_local('مرحبا שלום'.encode(),'text/plain'),'مرحبا שלום')
        text=extract_local(b'<p>Visible</p><script>secret()</script>','text/html')
        self.assertIn('Visible',text);self.assertNotIn('secret',text)
        with self.assertRaises(ValueError):extract_local(b'program','application/octet-stream')
        with self.assertRaises(ValueError):extract_local(b'a'*524289,'text/plain')

    def test_encrypted_pdf_is_not_decrypted_or_uploaded(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(100,100);writer.encrypt('private');data=io.BytesIO();writer.write(data)
        with self.assertRaises(ValueError):extract_local(data.getvalue(),'application/pdf')

    def test_bounded_plain_pdf_without_text_does_not_invent_evidence(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(100,100);data=io.BytesIO();writer.write(data)
        self.assertEqual(extract_local(data.getvalue(),'application/pdf'),'')


class QualityTests(unittest.TestCase):
    def test_multilingual_observable_meaning_survives_sender_changes(self):
        import importlib.util
        from pathlib import Path
        spec=importlib.util.spec_from_file_location('quality',Path(__file__).resolve().parents[1]/'scripts/evaluate_mail_quality.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        result=module.evaluate();self.assertEqual({e['id'] for e in result['errors']},{'needs-semantic-japanese-receipt'});self.assertEqual(result['semantic_abstentions'],3);self.assertEqual(result['incorrect_cleanup_eligibility'],0)
