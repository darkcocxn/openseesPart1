import openseespy.opensees as ops

# 清空之前的模型
ops.wipe()

# 模型创建：二维2自由度
ops.model('basic', '-ndm', 2, '-ndf', 2)

# 参数设定
num_floors = 10  # 总楼层数
floor_height = 3.0  # 每层楼的高度 (m)
E = 2.0e11  # 弹性模量 (Pa)
I = 0.0001  # 截面惯性矩 (m^4)
A = 0.1  # 截面面积 (m^2)
rho = 7850  # 材料密度 (kg/m^3)
mass = 1000.0  # 每层质量 (kg)

# 创建节点：每一层有一个节点
for i in range(num_floors + 1):
    ops.node(i, 0.0, i * floor_height)  # 节点的位置设置为水平方向0，竖直方向按层数递增

# 固定底部节点
ops.fix(0, 1, 1)  # 底部节点0固定所有自由度

# 材料定义：弹性材料
ops.uniaxialMaterial("Elastic", 1, E)  # 材料ID为1，弹性模量E

# 创建楼层之间的 `twoNodeLink` 元素
for i in range(num_floors):
    ops.element("twoNodeLink", i + 1, i, i + 1, "-mat", 1, "-dir", 1)  # 每层楼之间添加 `twoNodeLink` 元素

# 在第4层楼和第5层楼之间额外添加一个 `twoNodeLink` 元素
ops.element("twoNodeLink", 98, 3, 4, "-mat", 1, "-dir", 1)  # 在4和5层之间加入额外的阻尼器

# 定义质量：每一层的质量
for i in range(1, num_floors + 1):
    ops.mass(i, mass, mass, 0.0)  # 每层楼有质量，质量均匀分布

# 输出特征值
lambda_ = ops.eigen("-fullGenLapack", 1)
omega = lambda_[0] ** 0.5  # 特征频率

# Rayleigh 阻尼：设置阻尼比例
damping = 0.05  # 阻尼比
alpha = 2 * damping * omega  # alpha 为阻尼系数
ops.rayleigh(alpha, 0.0, 0.0, 0.0)  # 这里仅使用 alpha，beta 设置为0

# 读取地震波数据：来自于 accel.txt
ops.timeSeries("Path", 1, "-dt", 0.01, "-filePath", "accel.txt", "-factor", 9800)

# 定义地震波载荷模式：水平方向的地震波作用
ops.pattern("UniformExcitation", 1, 1, "-accel", 1, "-dof", 1)  # 水平方向的加速度

# 定义记录器：记录每一层的水平方向位移
for i in range(1, num_floors + 1):
    ops.recorder("Node", "-file", f"floor_disp/floor{i}_disp.txt", "-time", "-node", i, "-dof", 1, "disp")
#for i in range(1, num_floors + 1):
#    ops.recorder("Node", "-file", f"floor{i}_disp.txt", "-time", "-node", i, "-dof", 1, "disp")

# 设置分析方法
ops.system("BandGeneral")  # 使用带状求解器
ops.constraints("Plain")  # 简单约束
ops.numberer("Plain")  # 简单编号
ops.algorithm("Newton")  # 使用牛顿法
ops.integrator("Newmark", 0.5, 0.25)  # 使用 Newmark 时间积分法
ops.analysis("Transient")  # 进行瞬态分析

# 运行分析，假设地震波的时间步数为3000
num_steps = 3000  # 根据地震波的时间长度来确定分析步数
time_step = 0.01  # 时间步长设置为0.02秒
ops.analyze(num_steps, time_step)  # 运行瞬态分析
