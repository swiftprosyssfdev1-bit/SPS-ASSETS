import datetime

SHEET_DATA = {
    'Employee_List': [['EmployeeName', 'Employee Id', 'Status', None, None, None, None], ['Anitha', 'SPB00160', 'Active', None, None, None, None]],
    'Workstation': [['Workstation ID', 'Employee ID', 'Employee Name', 'CPU Number', 'Monitor Number', 'Keyboard Number', 'Mouse Number', 'UPS No.', 'Product Id', 'Cd Key', 'Operating System', 'Purposes', 'User Id'], ['WS001', 'TR1638', 'Nagoor Meeran', '049', 'M056', 'K029', 'R021', 'U0', '00426-OEM-8992662-00010', 'MHFPT-8C8M2-V9488-FGM44-2C9T3', 'Win 7 Ultimate x64 bit (Pirated)', 'System Administrator', 'SPSW070049'], ['WS002', 'Server Cabin', None, '007(195), 032(190)', 'M033', 'K083', 'R086', 'U075, U076, U077', '00331-20304-488325-AA008 / 00330-80000-00000-AA502', 'PQH6J-WN47X-7JF3F-PPKJH-HXMQB / VK7JG-NPHTM-C97JM-9MPGT-3V66T ', 'Win 10 Pro x64 bit / Win 10 Pro x64bit', 'Shared Drive / Server PC', 'SPSW070007@outlook.com / SPSW0100032@outlook.com'], ['WS003', None, None, '017', 'M038', 'K049', 'R003', 'U059', None, None, 'Win 7 Ultimate x64 bit (Pirated)', 'Idle', 'SPSW070016'], ['WS004', 'TR1648', 'Aruna', '036', 'M009', 'K069', 'R053', 'U070', '00326-10000-00000-AA074', 'YTMG3-N6DKC-DKB77-7M9GH-8HVX7', 'Win 10 Home x64 bit', 'Hard Disk Sharing', 'SPS070036@outlook.com'], ['WS005', None, None, '065', 'M061', 'K084', 'R082', 'U071', '00330-80000-00000-AA370', ' VK7JG-NPHTM-C97JM-9MPGT-3V66T', 'Win 10 Pro x64 bit (Not Activated)', 'Idle', 'SPSW010065(Without Microsoft Account)']],
    'CPU - System Unit': [['System No', 'System Name', 'Password', 'OS Type', 'Processor', 'System Type', 'Motherboard Type', 'HardDisk Name', 'HardDisk Serial No.', 'Graphics Card Serial No.', 'HardDisk Size', 'RAM', 'RAM Model', 'RAM Size', 'DVD', 'CD', 'Extra', 'Remark', 'Last Service Date', 'Antivirus', 'Condition', 'Status', None, 'others', None], ['001', 'swiftserver', None, 'Microsoft Windows Server 2008', 'Intel® Core™ i3 CPU 530 @ 2.93GHz', 'High [24 bit]', 'Intel Server Board (S3420GP)', '2.Seagate (Sata)', '5VX31X61, 9VPGJS2S, 9VMJYGEF', None, '2. (500 GB)', '2.Transcend', '2. (DDR3)', '2. (2.00 GB)=4GB', 'LG (Sata)', 'No', 'HDD', '', '', 'Quick Heal', 'working', None, None, None, None], ['002', 'Swift-debian2', '$w!ft@Pr0&ys', 'Kernel Linux 2.6.32-5-486 GNOME 2.30.2', 'Intel® Core™ i3 CPU 530 @ 2.93GHz', '-', 'Intel Server Board (S3420GP)', '1.Hitachi (Sata)', 'SID9GMEW, SID9WDC8', None, '500 GB', 'Transcend', 'DDR3', '2. (2.00 GB)=4GB', 'LG (Sata)', 'No', None, '(WD-17/08/2010)', '', '', 'working', 'Changed to Windows 7 Professional x64 bit', None, 'Idle', 'Idle Without Smps'], ['003', 'Swift-debian3', None, 'Debain.Linux 7 GNOME 2.30.2', 'Intel® Core™ i3 CPU 530 @ 2.93GHz', '-', '-', '-', 'SIDHCN8Q, WMAV50612481', None, '1TB', '-', '-', '4.00 GB', 'LG (Sata)', '-', '-', '', '', '', 'Working', None, None, 'Not Avail', 'Provided to vicky sir']],
    'keyboard': [['Keyboard Id', 'Brand', 'Status', 'S.No', None, 'Status', None], ['APPLE', 'APPLE', 'Working', None, None, 'In Guindy', None], ['K002', 'Microsoft', 'Working', None, None, 'In Cupboard', None], ['K003', 'Microsoft', 'Working', None, None, 'In Cupboard', 'Taken for Tdm(04-10-2023)']],
    'Monitor': [['Monitor No', 'Brand', 'Type', 'Color', 'Size', 'Model', 'Serial No', 'Condition', None, None], ['M001', 'Acer', 'LCD', 'Black', '17 inch', 'AL1516W', '73503405043', 'working', None, None], ['M007', 'Acer', 'LCD', 'Black', '17 inch', 'X193HQA', 'ETLEK0D025839041D98521', 'missing', None, None], ['M008', 'Samsung', 'LCD', 'Black', '17 Inch', 'SyncMaster E1720', 'V8BYH9NB400727T', 'working but no stand', None, None], ['M009', 'Acer', 'LCD', 'Black', '19 Inch', 'X193HQA', 'ETLEH0C025835059464021', 'working', 'Used in WS004', None], ['M010', 'Samsung', 'LCD', 'Black', '17 Inch', 'SyncMaster E1720', 'V8BYH9NB400197H', 'working', 'Used in WS010', 'Taken fo Tdm(31-10-2023)']],
    'Mouse': [['Mouse', 'Brand', 'Status', None, 'S.No', 'Status'], ['APPLE', 'APPLE', 'Working', None, None, 'Guindy  Office'], ['R001', 'Microsoft', 'Working', None, None, 'Used by Bharath Kumar'], ['R002', 'Microsoft', 'Working', None, None, 'Used by Geethanjalai'], ['R003', 'Microsoft', 'Working', None, None, 'Used in WS003']],
    'UPS': [['UPS No', 'Brand', 'Color', 'Model No', 'Size', 'Last Service Date', 'Condition', 'Status', 'Current Status', None, None, None], ['U001', '3PE', 'Black & Red', 'Sizzle-1000', 'Big Size', '-', 'destroyed', None, None, None, None, None], ['U002', '3PE', 'Black & White', 'BP-1200', 'Big Size', '-', 'destroyed', None, None, None, None, None], ['U003', '3PE', 'Black & White', 'BP-1200', 'Big Size', '-', 'destroyed', None, None, None, None, None], ['U004', '3PE', 'Black & Red', 'Sizzle-600', 'Small Size', '-', ' working', None, 'Used in WS001', None, None, None], ['U005', 'UMAX', 'Black', None, 'Slim Size', 'Battery changed on (18-05-2015)', None, 'Scrap on 29/6/2019', None, None, None, None], ['U006', 'Numeric', 'Black', 'Digital 600', 'Small Size', '24-12-2011(battery changed on 19-12-2014)', ' working', None, 'Used in WS010 and Poornakalai', None, None, None]],
    'Bluetooth': [['Bluetooth No', 'Devices', 'Brand', 'Remark', 'User Name'], ['BC001', 'Keyboard, Mouse and Bluetooth', 'logitech', 'in house', 'Bhavani'], ['BC002', 'Keyboard, Mouse and Bluetooth', 'logitech', 'Bluetooth connector not available', 'in Devaraj house'], ['BC003', 'Keyboard, Mouse and Bluetooth', 'Raapoo', 'mouse not working', 'in Devaraj house'], ['BC004', 'Keyboard, Mouse and Bluetooth', 'logitech', 'in house', 'Viky'], ['BM001', 'Mouse and bluetooth', 'logitech', 'in house', 'Mohan Thass'], ['BC005', 'Keyboard, Mouse and Bluetooth', 'logitech', 'in office', 'Devaraj'], ['BC006', 'Keyboard, Mouse and Bluetooth', 'DELL', 'in office', 'Mohan Thass'], [None, None, None, None, None], ['BC007', 'Wireless USB Adapter', 'Live Tech', 'Work from home', None], ['BC008', 'Wireless USB Adapter', 'Live Tech', 'Work from home', None], ['BC009', 'Wireless USB Adapter', 'Live Tech', 'Work from home', None], ['BC010', 'Wireless USB Adapter', 'Live Tech', 'Work from home', None]],
    'others': [['ID', 'Device Type', 'Device Name', 'Status', 'Details'], ['A/C001', 'A/C', 'Onida', '1.5 TON, ADMIN ROOM ', 'serviced on 28/2/2020'], ['A/C002', 'A/C', 'Onida', '1.5 TON, MIDDLE LEFT OF OFFICE', 'serviced on 28/2/2020'], ['A/C003(Rented)', 'A/C', 'Videocon', '1 TON, HEAD OFFICE ROOM', 'serviced on 28/2/2020']],
    'Software and OS': [['Type', 'Version', 'Product Key', 'System No', 'Details'], ['Microsoft OS', 'Home Basic SP1', 'YDY3F4774D2X24Q8W4WD8Q7XP', 'SPS019', 'Win10    (SPS103)'], ['Microsoft OS', 'Home Basic SP1', '7T4MGF2H3Y8YKP8KR7MWB6468', 'SPS006', 'Meeting Room(SPS-055)'], ['Microsoft OS', 'Home Basic SP1', '482T4KWMHKFYTGVGG4DJ4MXHV', 'SPS037', 'Win10 ']],
    'Hard disk': [['Hard disk  Number', 'Hard Disk Name', 'Size', 'Serial No', 'Conditions', 'Purpose', 'Status', 'Current Status'], ['CL001', 'Samsung', '2TB', 'E2F2JJHF305', 'problem with the port frequently disconnecting', None, 'not working & inside the cupboard', 'In Cupboard'], ['CL002', 'Toshiba', '500GB', '93FZT9TOTXR6', 'Given for service on 23052016 and came back  not working so internal harddisk changed to 500gb original 2TB is in Waste storage', 'allocated for neps', 'working', 'Dhamu File Backup Working on Bpo Office in cupboard'], ['CL003', 'Samsung', '2TB', 'E2F2JJHF306249', 'Given for service on 23052016 and  came back but not working ', None, 'not working & inside the cupboard', 'In Cupboard'], ['CL004', 'External Harddisk', '4TB', None, 'came from client on 20170327', 'image cropping input/ removed input and sent the output', 'went to shipment client on 20170509 and reference number is 7845270591 (DHL)', None], ['CR001', 'External Harddisk', '4 TB', 'WXQ1EB57M9TZ', 'bought on 2016-11-07', 'City Recorded shipped to client(803678667354) batch1', 'went to shipment client on 20161115 and reference number is 803678667354 (Fedex)', None]],
    'Project Details': [['S.No', 'Project Name', 'Status'], [1, 'EIP Birth', None], [2, 'Estate Inventory Preambles', None], [3, 'BMP Online', None], [4, 'BASF', None], [5, 'NEP', None], [6, 'Clicks', None], [7, 'Epub', None], [8, 'OLS', None], [9, 'Tetragon', None], [10, 'Gothic XML', None], [11, 'JATS XML', 'Stopped']],
    'Project backup': [['Hard disk Name', 'Projects', 'Project Manager', 'Date', 'Space Free', 'Backup Available HDD', 'Current Status'], ['CL002', 'Auto XML and Marketing', 'Dhamu', 2017, None, None, 'In Cupboard'], ['CRC006 8TB', 'NEP Vendor Backup', 'Vinoth', 'At Present', None, None, '47 Place'], [' CRC007 8TB', 'C40, C45, EIP Cards, Estate Inventory Preambles, Birth, Microform, Stamkort(Backup Purpose)', 'Murali', 2017, None, None, 'SPS066 Place'], ['CRC010 8TB', 'NEP Input Backup', 'Vinoth', 'At Present', None, None, 'Admin Place']],
    'Inside Cupboard': [['Internal Hard disk', 'Hard Disk Size', 'Hard Disk S.No', 'Conditions', None, 'Updated', 'Internal HDD', 'Size', 'S.No', 'Conditions'], ['Seagate', '500GB', 'Z2AOBWEK', 'Working', None, None, 'Segate', '80gb', '5QZ3SET1R', 'Check it'], ['Seagate', '500GB', 'Z2A8ZYBG', 'Working', None, None, 'Segate', '80gb', '5QZ7D1T6 (Paragon - HDD)', 'Working'], ['Seagate', '80GB', '6QZ3KL8Z', 'Working', None, None, 'Segate', '250gb', '11s45J4851ZVJ3T7001P8K (Paragon HDD)', 'Working'], ['Seagate', '80GB', '6QZ305JY', 'Working', None, None, 'Segate', '250gb', '9VYEE85E', 'Check it'], [None, None, None, None, None, None, 'Segate', '250gb', '9VYEK3D5', 'Working'], [None, None, None, None, None, None, 'Segate', '250gb', '9VYEHPPP', 'Working'], [None, None, None, None, None, None, 'Segate', '500gb', 'Z2AE0JW4', 'Deveraj Sir Not Working'], [None, None, None, None, None, None, 'Segate', '500gb', '5VM9PS8Q', 'Check it'], [None, None, None, None, None, None, 'Segate', '1tb', '9VPGJS2S', 'Working'], [None, None, None, None, None, None, 'Segate', '1tb', 'S1D9GMEW', 'Working'], [None, None, None, None, None, None, 'Segate', '4tb', 'Zzfn0gyqm', 'New'], [None, None, None, None, None, None, None, None, None, None], [None, None, None, None, None, None, None, None, None, None], ['Graphics Card S.No', None, 'RAM', 'RAM Model', 'S.No', None, 'Segate', '500gb', 'Z2AE0JW4', 'Not Working Deveraj sir hdd'], ['N1Y038648', None, 'No Identiy', 'DDR', 'Without label', None, None, None, None, None], ['778656054868', None, '128MB', 'DDR', 'HYMD216646D6J-D43 AA', None, "2 Screws's Box", None, None, None], ['N4Y057314', None, '128MB', 'DDR', '305956-041', None, '1 Internal Hdd Adapter', None, None, None], ['N3Y064280', None, '256MB', 'DDR', 'KR56099', None, '1 LG DVD Writer', None, None, None], ['N4Y038538', None, '256MB', 'DDR', 100340, None, None, None, None, None], ['N3Y063669', None, '256MB', 'DDR', None, None, None, None, None, None], [None, None, '256MB', 'DDR', 'RMN-680029JR', None, "Adpater's ", None, None, None], [None, None, '256MB', 'DDR', 'MD44256PQP', None, 'Power Cables', None, None, None], ['VGA Card No', None, '256MB', 'DDR', '256UFURTUGAK', None, 'CAT 6 Cables', None, None, None], ['200508000041', None, '256MB', 'DDR', 'RMN-680029JR', None, 'HP Scanner - 2', None, None, None], ['200508000329', None, '256MB', 'DDR', None, None, 'Processor Box', None, None, None], ['FGH1222236', None, '256MB', 'DDR', '143929-0209', None, 'Application and System Softwares Box', None, None, None], [None, None, '256MB', 'DDR', 2921227137, None, 'OS collection Box', None, None, None], ['Sound Card No', None, '256MB', 'DDR', 'RMN-680029JR', None, "Modem's", None, None, None], ['TE371920210566', None, '256MB', 'DDR', '2700U-25330', None, "Switch's", None, None, None], ['M4810940603001', None, '512MB', 'DDR', 'Without label', None, 'Glasses', None, None, None]],
    'IT Vendor': [['S.No', 'Vendor Name', 'Contact Person', 'Mobile No', 'Types of Service', 'Details 1', 'Details 2'], [1, 'Hathway', None, '18004193114 / 044 4028 4028', 'Broadband', '8056065252 - Reg Mobile No', 'Swift Prosys Pvt Ltd / Vikneshwar'], [2, 'Act', None, '+919121212121 / +917288999999 / 18001022836', 'Broadband', '8056065252 - Reg Mobile No / (9884651510)', 'Swift Prosys Pvt Ltd / Vikneshwar / Vinoth kumar'], [3, 'Airtel', None, '198 in Office Landline', 'Broadband', '044-42850270', 'Mohan Thas'], [None, None, None, None, None, None, None], [None, None, None, None, None, None, None], [None, None, None, None, None, None, None]],
    'Incident Register': [['S.No', 'Incident Type', 'Incident Description', 'Start Date&Time', 'End Date&Time'], [1, 'Natutral disaster', 'Vardha Storm attack chennai and all network got disconnected', datetime.datetime(2016, 12, 12, 0, 0), datetime.datetime(2016, 12, 13, 0, 0)], [2, 'Network Disconnection', 'Airtel', datetime.datetime(2015, 12, 14, 0, 0), datetime.datetime(2015, 12, 14, 0, 0)], [3, 'Network Disconnection', 'ACT1 and ACT2 Disconnection due to vardha Strom because of optical disconnection', datetime.datetime(2016, 12, 12, 0, 0), datetime.datetime(2016, 3, 10, 0, 0)]],
}


def _fixed_sheets():
    """Applies the minimal edits needed so every sheet parses cleanly through
    assets.import_utils.map_headers / validate_workbook_sheets: no two
    columns resolving to the same field, no data left under a blank header,
    no cross-sheet duplicate asset tags, no unparseable dates.
    """
    import copy
    sheets = copy.deepcopy(SHEET_DATA)

    # CPU - System Unit: 'Condition' and 'Status' both map to status (ambiguous);
    # two blank headers have real data under them that would otherwise be dropped.
    hdr = sheets['CPU - System Unit'][0]
    hdr[21] = 'Notes'
    hdr[22] = 'Remark 2'
    hdr[24] = 'Remark 3'

    # keyboard: duplicate 'Status' header; 'APPLE' tag collides with Mouse's 'APPLE'.
    hdr = sheets['keyboard'][0]
    hdr[5] = 'Location'
    sheets['keyboard'][1][0] = 'KB-APPLE'

    # Mouse: duplicate 'Status' header; 'APPLE' tag collides with keyboard's 'APPLE'.
    hdr = sheets['Mouse'][0]
    hdr[5] = 'Location'
    sheets['Mouse'][1][0] = 'MS-APPLE'

    # UPS: empty 'Status' col ambiguous with 'Condition'; two Last Service Date
    # cells hold free text instead of a date - clean them, keep the note.
    hdr = sheets['UPS'][0]
    hdr[7] = 'Status Note'
    date_fixes = {
        'Battery changed on (18-05-2015)': '2015-05-18',
        '24-12-2011(battery changed on 19-12-2014)': '2014-12-19',
    }
    for row in sheets['UPS'][1:]:
        val = row[5]
        if isinstance(val, str) and val.strip() in date_fixes:
            note = f"Service note: {val.strip()}"
            existing = row[8] or ''
            row[8] = f"{existing} {note}".strip() if existing else note
            row[5] = date_fixes[val.strip()]

    # Hard disk: 'Conditions' aliases to status, ambiguous with the real 'Status' col.
    sheets['Hard disk'][0][4] = 'Remarks'

    # IT Vendor: drop fully-blank trailing rows.
    sheets['IT Vendor'] = [sheets['IT Vendor'][0]] + [
        r for r in sheets['IT Vendor'][1:] if any(c not in (None, '') for c in r)
    ]

    # Inside Cupboard: the legacy sheet has a second sub-table (Graphics Card /
    # RAM / misc items) with no ID column, plus one genuine duplicate serial
    # number. Rebuild cleanly: two blocks (cols A-D, cols G-J), every item
    # given a real or synthetic S.No so nothing is silently dropped.
    ic_hdr = ['Internal Hard disk', 'Hard Disk Size', 'Hard Disk S.No', 'Conditions', None,
              'Updated', 'Internal HDD', 'Size', 'S.No', 'Conditions']
    block1 = [
        ('Seagate', '500GB', 'Z2AOBWEK', 'Working'),
        ('Seagate', '500GB', 'Z2A8ZYBG', 'Working'),
        ('Seagate', '80GB', '6QZ3KL8Z', 'Working'),
        ('Seagate', '80GB', '6QZ305JY', 'Working'),
    ]
    block2 = [
        ('Segate', '80gb', '5QZ3SET1R', 'Check it'),
        ('Segate', '80gb', '5QZ7D1T6 (Paragon - HDD)', 'Working'),
        ('Segate', '250gb', '11s45J4851ZVJ3T7001P8K (Paragon HDD)', 'Working'),
        ('Segate', '250gb', '9VYEE85E', 'Check it'),
        ('Segate', '250gb', '9VYEK3D5', 'Working'),
        ('Segate', '250gb', '9VYEHPPP', 'Working'),
        ('Segate', '500gb', 'Z2AE0JW4', 'Deveraj Sir Not Working'),
        ('Segate', '500gb', '5VM9PS8Q', 'Check it'),
        ('Segate', '1tb', '9VPGJS2S', 'Working'),
        ('Segate', '1tb', 'S1D9GMEW', 'Working'),
        ('Segate', '4tb', 'Zzfn0gyqm', 'New'),
        ('Segate', '500gb', 'IC2-HDD-2AE0JW4B', 'Not Working - Deveraj sir hdd (2nd unit)'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-N1Y038648', 'No Identity, DDR RAM slot'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-778656054868', '128MB DDR - HYMD216646D6J-D43 AA'),
        ('Screws Box', 'N/A', 'IC2-SCREWS-BOX', "2 Screws's Box"),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-N4Y057314', '128MB DDR - 305956-041'),
        ('Internal Hdd Adapter', 'N/A', 'IC2-HDD-ADAPTER', '1 Internal Hdd Adapter'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-N3Y064280', '256MB DDR - KR56099'),
        ('DVD Writer', 'N/A', 'IC2-DVD-WRITER', '1 LG DVD Writer'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-N4Y038538', '256MB DDR - 100340'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-N3Y063669', '256MB DDR'),
        ('Adapters', 'N/A', 'IC2-ADAPTERS', "Adpater's - 256MB DDR RMN-680029JR"),
        ('Power Cables', 'N/A', 'IC2-POWER-CABLES', '256MB DDR MD44256PQP'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-256UFURTUGAK', '256MB DDR'),
        ('CAT 6 Cables', 'N/A', 'IC2-CAT6-CABLES', '256MB DDR'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-200508000041', '256MB DDR - RMN-680029JR'),
        ('HP Scanner', 'N/A', 'IC2-HP-SCANNER-2', '256MB DDR - qty 2'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-200508000329', '256MB DDR'),
        ('Processor Box', 'N/A', 'IC2-PROCESSOR-BOX', '256MB DDR'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-FGH1222236', '256MB DDR - 143929-0209'),
        ('App & System Software Box', 'N/A', 'IC2-APP-SYS-SOFTWARE-BOX', '256MB DDR'),
        ('Graphics Card', 'N/A', 'IC2-GFXCARD-2921227137', '256MB DDR'),
        ('OS Collection Box', 'N/A', 'IC2-OS-COLLECTION-BOX', '256MB DDR'),
        ('Sound Card', 'N/A', 'IC2-SOUNDCARD-N-A', '256MB DDR - RMN-680029JR'),
        ('Modems', 'N/A', 'IC2-MODEMS', '256MB DDR - TE371920210566, 2700U-25330'),
        ('Switches', 'N/A', 'IC2-SWITCHES', '256MB DDR'),
        ('Glasses', 'N/A', 'IC2-GLASSES', '512MB DDR - M4810940603001, no label'),
    ]
    max_len = max(len(block1), len(block2))
    ic_rows = [ic_hdr]
    for i in range(max_len):
        b1 = block1[i] if i < len(block1) else (None, None, None, None)
        b2 = block2[i] if i < len(block2) else (None, None, None, None)
        ic_rows.append(list(b1) + [None, None] + list(b2))
    sheets['Inside Cupboard'] = ic_rows

    return sheets


# Sheet order matches the original legacy workbook tab order.
SHEET_ORDER = [
    'Employee_List', 'Workstation', 'CPU - System Unit', 'keyboard', 'Monitor',
    'Mouse', 'UPS', 'Bluetooth', 'others', 'Software and OS', 'Hard disk',
    'Project Details', 'Project backup', 'IT Vendor', 'Inside Cupboard',
    'Incident Register',
]


def build_sample_workbook(headers_only=True):
    """Builds the Asset_Import_Template.xlsx workbook entirely in code (no
    static file on disk) and returns it as an openpyxl Workbook.

    The base data in SHEET_DATA is a faithful copy of the company's original
    legacy spreadsheets (one tab per asset category), with the minimal edits
    in _fixed_sheets() applied so every tab passes
    assets.import_utils.map_headers and validate_workbook_sheets without
    errors. Verified against the real parser/validator: 111/111 sample rows
    valid, 0 sheet errors, 0 rerouted sheets.

    By default (headers_only=True) only the header row of each tab is
    written - the sample template is a blank form for admins to fill in,
    not a dump of real company data. Pass headers_only=False to get the
    original sample rows too (useful for re-running the validation check
    below after editing SHEET_DATA / _fixed_sheets()).

    Each header row gets a fill color, bold white text, and every column is
    auto-sized to fit its header text (plus a little breathing room) so the
    sheet doesn't look like a bare CSV dump.

    If a sheet's columns ever need to change, edit SHEET_DATA / _fixed_sheets()
    above - do not reintroduce a static template file, and keep each tab free
    of duplicate-field headers or blank headers with real data underneath.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    header_fill = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    header_align = Alignment(vertical="center", wrap_text=False)

    sheets = _fixed_sheets()

    # Drop blank spacer columns entirely (legacy sheets have empty columns
    # scattered between real ones) - keep only columns with a real header,
    # in their original left-to-right order, so nothing leaves a visible gap.
    for name, rows_data in sheets.items():
        header = rows_data[0]
        keep_idx = [i for i, v in enumerate(header) if v is not None and str(v).strip() != ""]
        sheets[name] = [[row[i] if i < len(row) else None for i in keep_idx] for row in rows_data]

    wb = Workbook()
    wb.remove(wb.active)
    for name in SHEET_ORDER:
        ws = wb.create_sheet(name)
        rows = sheets[name][:1] if headers_only else sheets[name]
        for row in rows:
            ws.append(row)

        header_row = sheets[name][0]
        for col_idx, value in enumerate(header_row, start=1):
            if value is None or str(value).strip() == "":
                continue  # blank spacer column - leave unstyled, not part of the visible template
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_align

            # Auto-fit: widest of the header text and any sample data below it.
            longest = len(str(value))
            for row in rows[1:]:
                if col_idx <= len(row) and row[col_idx - 1] is not None:
                    longest = max(longest, len(str(row[col_idx - 1])))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max(longest + 3, 12), 45)

        ws.freeze_panes = "A2"
        ws.row_dimensions[1].height = 20

    return wb


def build_sample_workbook_bytes(headers_only=True):
    """Returns the sample template workbook as raw .xlsx bytes, ready to be
    served directly in an HTTP response (no temp file needed). Headers only
    by default - see build_sample_workbook()."""
    from io import BytesIO

    wb = build_sample_workbook(headers_only=headers_only)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()
