from PIL import Image
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'I:\Program Files\Tesseract-OCR\tesseract.exe'
image = Image.open("./attack.jpg")
text = pytesseract.image_to_string(image)
print(text)