from PIL import Image
import OpenCV
basewidth = 1520
img = Image.open("C:/laptop/Zuid Afrika/web/biotablog/Blog Pictures 2024-07-05-2024-07-05-biota-0-5/2024 Zuid Afrika-N7050177-ORF-DxO_DeepPRIME XD2.jpg")
wpercent = (basewidth/float(img.size[0]))
hsize = int((float(img.size[1])*float(wpercent)))
img2 = img.resize((basewidth, hsize))
img2.save("C:/laptop/Zuid Afrika/web/biotablog/Blog Pictures 2024-07-05-2024-07-05-biota-0-5/2024 Zuid Afrika-N7050177-ORF-rescale_pil_1520 XD2.jpg")