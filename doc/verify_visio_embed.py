import zipfile
import os
import xml.etree.ElementTree as ET
import sys

def verify_docx(file_path):
    print(f"=== 验证文档: {file_path} ===")
    if not os.path.exists(file_path):
        print(f"错误: 文件不存在: {file_path}")
        return False

    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            file_list = zf.namelist()
            
            # 1. 检查 embeddings 目录
            embeddings = [f for f in file_list if f.startswith('word/embeddings/')]
            print(f"发现嵌入对象: {len(embeddings)}")
            for e in embeddings:
                print(f"  - {e} ({zf.getinfo(e).file_size} bytes)")
            
            # 2. 检查关系文件
            rel_path = 'word/_rels/document.xml.rels'
            if rel_path in file_list:
                with zf.open(rel_path) as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    ns = {'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}
                    ole_rels = []
                    for r in root.findall('rel:Relationship', ns):
                        if 'oleObject' in r.get('Type', ''):
                            ole_rels.append((r.get('Id'), r.get('Target')))
                    print(f"发现 OLE 关系: {len(ole_rels)}")
                    for rid, target in ole_rels:
                        print(f"  - ID: {rid}, Target: {target}")
            else:
                print(f"错误: 未找到关系文件 {rel_path}")

            # 3. 检查 document.xml 中的结构
            doc_path = 'word/document.xml'
            if doc_path in file_list:
                with zf.open(doc_path) as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    ns = {
                        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                        'o': 'urn:schemas-microsoft-com:office:office',
                        'v': 'urn:schemas-microsoft-com:vml'
                    }
                    objects = root.findall('.//w:object', ns)
                    print(f"发现 w:object 元素: {len(objects)}")
                    for i, obj in enumerate(objects):
                        print(f"  对象 {i+1}:")
                        shape = obj.find('v:shape', ns)
                        if shape is not None:
                            print(f"    - 包含 v:shape (ID: {shape.get('id')}, Style: {shape.get('style')})")
                        else:
                            print("    - 警告: 缺失 v:shape 容器")
                        
                        ole_obj = obj.find('o:OLEObject', ns)
                        if ole_obj is not None:
                            print(f"    - 包含 o:OLEObject (ProgID: {ole_obj.get('ProgID')}, r:id: {ole_obj.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')})")
                        else:
                            print("    - 错误: 缺失 o:OLEObject 定义")
            else:
                print(f"错误: 未找到主文档文件 {doc_path}")

    except Exception as e:
        print(f"发生异常: {e}")
        return False
    
    print("=== 验证完成 ===")
    return True

if __name__ == "__main__":
    target_file = sys.argv[1] if len(sys.argv) > 1 else "output/search_top1.docx"
    verify_docx(target_file)
