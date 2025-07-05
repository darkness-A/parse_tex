import json
import os
import re
import shutil
from pathlib import Path
from app.common import pyRitoFile, hash_helper
from loguru import logger

def normalize_path(path: str) -> str:
    """
    使用标准库完全标准化路径格式
    :param path: 原始路径(可以是任何分隔符格式)
    :return: 当前系统标准格式的绝对路径
    """
    # 使用pathlib初步处理
    p = Path(path.strip())
    # 转换为绝对路径并标准化
    return os.path.normpath(os.path.abspath(str(p)))
class FIEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, '__json__'):
            return obj.__json__()
        elif isinstance(obj, bytes):
            return str(obj.hex(' ').upper())
        else:
            return json.JSONEncoder.default(self, obj)
def find_data_root(start_path: str, max_depth=10) -> str:
        """
        从指定路径向上查找 data 目录
        :param start_path: 起始路径 (可以是文件或目录)
        :param max_depth: 最大向上查找层级 (防止无限循环)
        :return: 找到的 data 目录绝对路径，未找到则返回 None
        """
        current_path = os.path.abspath(start_path)

        for _ in range(max_depth):
            # 检查当前路径的 basename
            if os.path.basename(current_path).lower() == "data":
                return current_path

            # 检查当前路径的子目录
            for root, dirs, _ in os.walk(current_path):
                if "data" in [d.lower() for d in dirs]:
                    return os.path.join(root, "data")

            parent_path = os.path.dirname(current_path)
            if parent_path == current_path:  # 已到根目录
                break
            current_path = parent_path

        return None

class SkinParser:
    def __init__(self, bin_path):
        self.links = None
        self.links_read_cache= {}
        self.json_data = None
        self.entries=None
        self.entries_index = None

        self.hash_table={}
        self.bin_path = normalize_path(bin_path)
        self.material_dict = {}
        self.base_texture = ""
        self.hide_list = []
        self.skin_id=None

    def _build_nested_index(self, data):
        """递归构建嵌套数据索引"""
        index = {}
        if isinstance(data, dict):
            # 处理有hash字段的对象
            if 'hash' in data and data['hash']:
                index[data['hash']] = data.get('data', None)

            # 递归处理所有值
            for key, value in data.items():
                nested = self._build_nested_index(value)
                if nested:  # 合并嵌套索引
                    index.update(nested)

        elif isinstance(data, list):
            # 处理列表中的每个元素
            for item in data:
                nested = self._build_nested_index(item)
                if nested:
                    index.update(nested)

        return index

    def _build_full_index(self,json_data):
        """构建完整索引(外部+内部)"""
        full_index = {}
        for entry in json_data.get('entries', []):
            # 外部索引
            if 'hash' in entry and 'data' in entry:
                full_index[entry['hash']] = entry['data']
                # 内部索引
                internal_index = self._build_nested_index(entry['data'])
                full_index.update(internal_index)
        return full_index
    def parse(self):
        """主解析流程"""
        #解析资源路径
        self._parse_path()
        #初始化哈希库
        hash_helper.read_bin_hashes()
        self.hash_table=hash_helper.Storage.hashtables
        #将bin解析为json
        self.parse_bin2json()
        #解析英雄基础数据
        self._parse_character_data()
        #解析材质贴图
        self._parse_mesh_properties()
        self.export_json()
    def _parse_path(self):
        #首先获取根路径，也就是assets和data所在路径
        root_path=find_data_root(self.bin_path)
        if not root_path:
            raise Exception("未找到资源根目录")
        self.root_path = os.path.dirname(root_path)
        logger.info(f"查找资源根路径:{self.root_path}")
    def parse_bin2json(self):
        obj=pyRitoFile.bin.BIN().read(self.bin_path)
        obj.un_hash(self.hash_table)
        json_dump = json.dumps(obj, indent=4, ensure_ascii=False, cls=FIEncoder)
        self.json_data=json.loads(json_dump)
        #将links保存，后续查找links使用
        self.links=self.json_data.get("links",[])
        logger.debug(f"解析bin为json完成,导入links数量为:{len(self.links)}")
        self.entries=self.json_data.get("entries",None)
        if not self.entries:
            raise Exception("json中未找到entries")
        #构建索引方便数据查找
        self.entries_index=self._build_full_index(self.json_data)


    def _parse_character_data(self):
        """解析角色基础数据（皮肤名、默认材质等）"""
        SkinCharacterDataProperties=None
        for data in self.entries:
            if data["type"]=="SkinCharacterDataProperties":
                SkinCharacterDataProperties= data["hash"]
                logger.info(f"解析到皮肤定义结构体:{SkinCharacterDataProperties}")
                break
        if SkinCharacterDataProperties is not None:
            #判断哈希是否已经匹配到字符串
            if "Characters" in SkinCharacterDataProperties:
                #提取角色名称和皮肤编号
                split_data=SkinCharacterDataProperties.split("/")
                self.champion_name=split_data[1]
                self.skin_id=split_data[-1].replace("skin","").replace("Skin","")
            else:
                logger.warning(f"SkinCharacterDataProperties可能为哈希，尝试从路径获取英雄名称和皮肤编号：{SkinCharacterDataProperties}")
            if self.skin_id is None:
                # 从路径中获取英雄名称和皮肤编号
                #解析bin路径，将bin_path进行格式化后分割
                this_bin_path=self.bin_path.split("\\")
                self.champion_name=this_bin_path[-3]
                self.skin_id=this_bin_path[-1].replace(".bin","").replace("skin", "")
        # 由于assets和data目录使用id命名不同，需要兼容
        # 判断skin_id是否超过10，没有超过10需要加0
        if  int(self.skin_id)==0:
            self.skin_id_assets="base"
        elif int(self.skin_id) < 10:
            logger.debug(f"skin_id:{self.skin_id}小于10，需要加0")
            self.skin_id_assets = f"skin0{self.skin_id}"
        else:
            self.skin_id_assets = f"skin{self.skin_id}"
        #构造贴图路径
        self.texture_root_path = f'{self.root_path}/ASSETS/Characters/{self.champion_name}/Skins/{self.skin_id_assets}'


        logger.success(f"解析角色 {self.champion_name} 皮肤ID {self.skin_id} 贴图路径:{self.texture_root_path}")
        #解析骨架路径
        self.skeleton = self.entries_index.get('skeleton')
        if self.skeleton is None:
            raise ValueError("未匹配到骨架")
        logger.success(f"匹配到骨架: {self.skeleton}")
        #解析网格路径
        self.simpleSkin=self.entries_index.get('simpleSkin')
        if self.simpleSkin is None:
            raise ValueError("未匹配到网格")
        logger.success(f"匹配到网格文件: {self.simpleSkin}")
        self._find_skn_submesh()
        #解析动画
        self._parse_animations()
    def _find_skn_submesh(self):
        #通过解析skn文件获取网格
        #判断网格文件是否存在
        skn_file=f'{self.root_path}//{self.simpleSkin}'
        if not os.path.exists(skn_file):
            raise ValueError("未找到网格文件")
        skn_obj = pyRitoFile.skn.SKN().read(skn_file)
        skn_json_data = json.dumps(skn_obj, indent=4, ensure_ascii=False, cls=FIEncoder)
        skn_json_data = json.loads(skn_json_data)
        sub_mesh_list=[]
        # 从skn_json获取网格
        for mesh in skn_json_data["submeshes"]:
            mesh_name = mesh["name"]
            self.material_dict[mesh_name] = {}
            sub_mesh_list.append(mesh_name)
        logger.success(f'获取网格完成，网格列表为：{sub_mesh_list}')
        #获取需要隐藏的网格列表
        initialSubmeshToHide=self.entries_index.get('initialSubmeshToHide', '')
        self.hide_list = initialSubmeshToHide.split(" ")
        logger.success(f'获取默认隐藏网格列表完成，列表为：{self.hide_list}')
    def _parse_mesh_properties(self):
        logger.debug(f'开始解析网格材质')
        """解析网格材质和隐藏列表"""
        mesh_properties = self.entries_index.get("skinMeshProperties", [])
        for prop in mesh_properties:
            if prop.get("hash") == "material":
                logger.info(f'获取默认材质完成，类型:Link：{prop["data"]}')
                link_rs = self._resolve_material_link(prop["data"])
                # for t in link_rs:
                #     if t["type"]=="Diffuse_Texture" or t["type"]=="DiffuseTexture":
                self.base_texture=link_rs
                logger.success(f'从Link获取默认材质贴图完成：{self.base_texture}')
                break
        if len(self.base_texture) ==0:
            logger.error("未找到material默认材质,尝试从texture匹配")
            for prop in mesh_properties:
                if prop.get("hash") == "texture":
                    texture=prop["data"]
                    logger.success(f'从texture获取默认材质贴图完成：{texture}')
                    self.base_texture=texture
        # 2. 解析子材质
        for override in self.entries_index.get("materialOverride", []):
            self._parse_material_override(override)

    def _parse_material_override(self, override_data):
        """标准化解析子材质"""
        data = override_data.get("data")
        texture_dict = {"submesh": "", "tex_type": "",'tex_path':""}
        for data_item in data:
            if data_item["hash"]=="submesh":
                texture_dict["submesh"] = data_item["data"]
            elif data_item["hash"]=="texture":
                texture_dict["tex_type"] = "texture"
                texture_dict["tex_path"] = data_item["data"]
            elif data_item['hash']=="material":
                texture_dict["tex_type"] = "material"
                texture_dict["tex_path"] = data_item["data"]

        if texture_dict["tex_type"]=="material":
            link=texture_dict["tex_path"]
            #从link提取贴图
            link_rs=self._resolve_material_link(link)
            # for t in link_rs:
            #     #if t["type"] == "Diffuse_Texture" or "DiffuseTexture"==t["type"]:
            texture_dict["tex_path"] = link_rs
            logger.success(f"从Link获取子材质贴图完成：{link_rs}")
        #将材质贴图加入字典
        submesh=texture_dict["submesh"]
        #只保存网格中存在的
        if submesh in self.material_dict:
            self.material_dict[submesh]["texture"]=texture_dict["tex_path"]
    def _resolve_material_link(self, link):
        """解析材质链接中的贴图路径"""
        logger.info(f"尝试从Link获取贴图:{link}")
        texture_list=[]
        linked_data = self.entries_index.get(link, [])
        if len(linked_data)==0:
            logger.warning(f"材质链接:{link}未找到，尝试从导入Link中查找")
            for import_link in self.links:
                #读取前判断是否已经加载过
                readed=self.links_read_cache.get(import_link, False)
                if not readed:
                    logger.info(f"缓存中没有加载Link:{import_link}")
                    #开始加载bin文件
                    bin_file_path=f"{self.root_path}/{import_link}"
                    #判断是否存在
                    if not os.path.exists(bin_file_path):
                        logger.warning(f"不存在,大概率由于windows文件长度限制，开始读取hashed_files.json，无法加载的文件为:{bin_file_path}")
                        hashed_files_path=f"{self.root_path}/hashed_files.json"
                        with open(hashed_files_path, 'r', encoding='utf-8') as f:
                            hashed_files = json.load(f)
                        for bin in hashed_files:
                            if hashed_files[bin].lower()==import_link.lower():
                                bin_file_path=f"{self.root_path}/{bin}"
                                logger.success(f"匹配长文件名link_bin成功:{bin_file_path}")
                    logger.debug(f"正在加载{bin_file_path}")
                    obj = pyRitoFile.bin.BIN().read(bin_file_path)
                    obj.un_hash(self.hash_table)
                    json_dump = json.dumps(obj, indent=4, ensure_ascii=False, cls=FIEncoder)
                    json_data = json.loads(json_dump)
                    index=self._build_full_index(json_data)
                    # for i in index:
                    #     print(i)
                    self.links_read_cache[import_link]= index
               # print(json.dumps(self.links_read_cache[import_link]))
                else:
                    logger.debug("已有link缓存，直接读取")
                linked_data = self.links_read_cache[import_link].get(link, [])
                if len(linked_data) > 0:
                    logger.success(f"成功从外部Bin加载到Link数据:{link}")
                    break
        logger.debug("开始从Link数据中匹配贴图")
        for item in linked_data:
            if item.get("hash") == "samplerValues":
                logger.debug("匹配到samplerValues")
                for sampler in item.get("data", []):
                    #有的皮肤存在多张贴图的情况
                    data=sampler.get("data")
                    texture_dict={"type":"","path":""}
                    for data_item in data:
                        if data_item["hash"]=="textureName":
                            texture_dict["type"]=data_item["data"]
                        elif data_item["hash"]=="texturePath":
                            texture_dict["path"]=data_item["data"]
                    if texture_dict["type"]!="":
                        #由于Diffuse字段有太多种，只能模糊匹配
                        #if "Diffuse_Texture" == texture_dict["type"] or "DiffuseTexture"==texture_dict["type"]: #Diffuse_Color
                        if "Diffuse" in texture_dict["type"]:
                            texture_list.append(texture_dict)
        texture_num=len(texture_list)
        if texture_num>0:
            rs=texture_list[0]["path"]
            logger.info(f"材质链接内贴图:{rs},总数量:{texture_num}")
            #logger.info(f"材质链接内贴图数量:{texture_num}")
            #只返回第一个，因为有部分Diffuse贴图不是真正的默认贴图
            return rs
            #return texture_list
        return None

    def _parse_animations(self):
        """处理动画路径逻辑"""
        anim_data = self.entries_index.get("animationGraphData", [])
        if not anim_data:
            raise ValueError("没有获取到动画Link数据")
        logger.debug("开始获取动画路径")
        #判断Link是否为哈希
        if "Animations" not in anim_data:
            logger.warning(f"动画路径错误，可能为哈希:{anim_data}")
            #尝试计算哈希进行对撞
            #构造字符串
            anim_path_str=f'Characters/{self.champion_name}/Animations/Skin{self.skin_id}'
            anim_hash=pyRitoFile.bin.BINHasher.raw_to_hex(anim_path_str)
            if anim_hash!=anim_data:
                logger.warning(f"动画路径哈希对撞失败,解析完成后需要手动选定动画路径，字符串：{anim_path_str},生成哈希：{anim_hash},所需哈希：{anim_data}")
            else:
                logger.success(f"动画路径哈希对撞成功,字符串：{anim_path_str},生成哈希:{anim_hash},所需哈希{anim_data}")
                #读取动画bin解析动画调用的真实路径
                anim_path = f'{self.root_path}/data/{anim_path_str}.bin'
                #判断动画bin文件是否存在
        else:
            anim_path_str=anim_data
            anim_path = f'{self.root_path}/data/{anim_path_str}.bin'

        if not os.path.exists(anim_path):
            logger.warning(f"动画bin路径不存在:{anim_path}")
        else:
            # 读取动画bin，解析动画真实路径
            animation_bin_obj = pyRitoFile.bin.BIN().read(anim_path)
            animation_bin_obj.un_hash(self.hash_table)
            json_dump = json.dumps(animation_bin_obj, indent=4, ensure_ascii=False, cls=FIEncoder)
            json_data = json.loads(json_dump)
            index=self._build_full_index(json_data)
            mAnimationFilePath=index.get('mAnimationFilePath',None)
            if mAnimationFilePath is None:
                logger.warning(f"动画bin未找到mAnimationFilePath字段")
            else:
                mAnimationFilePath=os.path.dirname(mAnimationFilePath)
                self.animation_path=mAnimationFilePath
                logger.success(f"动画资源真实路径: {mAnimationFilePath}")



    def _build_mesh_list(self):
        mesh_data_list=[]
        """构建网格数据列表（含隐藏状态）"""
        for mesh_name in self.material_dict:
            # 拷贝所有贴图到皮肤资源根目录(由于个别贴图在特效路径)
            #判断网格是否存在贴图，如果不存在就使用默认贴图
            if self.material_dict[mesh_name].get("texture","")=="":
                logger.warning(f"{mesh_name}网格不存在贴图,使用默认贴图")
                self.material_dict[mesh_name]["texture"]=self.base_texture
            source_path=f'{self.root_path}/{self.material_dict[mesh_name]['texture']}'

            source_path = os.path.normpath(source_path.replace('/', '\\'))  # 统一转换为Windows路径
            dest_path = os.path.normpath(self.texture_root_path.replace('/', '\\'))
            try:
                shutil.copy(source_path, dest_path)
            except shutil.SameFileError:
                pass
            #格式化让mindcorpviewer识别
            text_name = self.material_dict[mesh_name]['texture'].split('/')[-1].replace('.tex', "").replace(".dds","").lower()
            show = True
            #遍历隐藏列表
            for hide_mesh_str in self.hide_list:
                if mesh_name == hide_mesh_str:
                    show = False
            mesh_data = {
                "Show": show,
                mesh_name: text_name
            }
            mesh_data_list.append(mesh_data)
        return mesh_data_list
    def export_json(self):
        data={
            "MSAA": 4,
            "Vsync": False,
            "ShowFloor": True,
            "ShowSkybox": True,
            "SynchronizedTime": True,
            "ScreenShotResolution": [
                1920,
                1080
            ],
            "PATHS":
            [
                {
                "Name": self.champion_name+self.skin_id,
                "Skin": os.path.join(self.root_path, self.simpleSkin),
                "Skeleton": os.path.join(self.root_path, self.skeleton),
                "Textures": self.texture_root_path,
                "Animations": os.path.join(self.root_path, self.animation_path)
                }
            ],
            "OPTIONS":
                [
                    {
                        "Show": True,
                        "ShowWireframe": False,
                        "ShowSkeletonNames": False,
                        "ShowSkeletonBones": False,
                        "ShowSkeletonJoints": False,
                        "UseAnimation": False,
                        "PlayAnimation": False,
                        "LoopAnimation": True,
                        "NextAnimation": False,
                        "AnimationTime": 0.0,
                        "AnimationSpeed": 1.0,
                        "SelectedAnimation": "",
                        "PositionOffset": [0.0,0.0,0.0]
                    }
                ],
            "MESHES": [
                self._build_mesh_list()
            ]
        }
        with open("config.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.success("写出配置json成功")
#正常情况下是传入一个bin文件，然后解析出路径
bin=r"E:\myapp\lol\edit\wad_edit\亚托克斯\data\characters\aatrox\skins\skin33.bin"
parser = SkinParser(bin)
parser.parse()
