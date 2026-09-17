import os
import sys
import psutil
import time
import influx_exporter as ife
import lissozis as ls
import json
import datetime
filein = os.getcwd() + "/"


def writer(logw):
    global filein
    logd = datetime.datetime.now()
    logd = str(logd)
    logx = {}
    logx[logd] = str(logw)
    datew=datetime.datetime.now().date()
    datew=str(datew)
    file=filein + datew +".json"
    with open(file,"a") as fdr:
        json.dump(logx, fdr)

    x = open(file,"r")
    lastview=x.read()
    x.close()
    x = open(file, "w")
    lastview=str(lastview) + "\n"
    x.write(lastview)
    x.close()


def logcontr():
    global filein
    now = datetime.datetime.now().date()
    back = now-datetime.timedelta(14)
    #print(now,back)
    infiles=os.listdir(filein)
    #print(infiles)
    wfile=[]
    for t in infiles:
        if t[-5:] ==".json" and t != "token.json" and t != "config.json":
            wfile.append(t)
    #print(wfile)
    for u in wfile:

        u_new=u[:-5]
        datetimeobj = datetime.datetime.strptime(u_new, '%Y-%m-%d')
        datetimeobj = datetimeobj.date()
        #print(datetimeobj,back)
        if datetimeobj < back:
            #print(u_new)
            os.remove(filein+str(u))


try:
    with open("./config.json", "r") as rddf:
        jsrdd = json.load(rddf)
        wait_time = jsrdd["wait_time"]
        wait_time = int(wait_time)
        print(wait_time)
except Exception as eep:
    writer("config dosyası okunurken hata oluştu")
    sys.exit()
else:
    print("Config Dosya okuma başarılı")
backram_diskread = []
backram_diskwrite = []
try:
    while(True):
        std = time.time()
        logcontr()
        allprocess={}
        allprocess_ram = {}
        allprocess_diskread = {}
        allprocess_diskwrite = {}
        allprocess_networkusagesend = {}
        allprocess_networkusagerecieve = {}

        for process in psutil.process_iter():
            try:
                #print(process.username(),process.name(),process.cpu_percent(interval=1),process.memory_percent())
                allprocess[process.name()] = process.cpu_percent(interval=0)/psutil.cpu_count()
                allprocess_ram[process.name()] = process.memory_percent()
                allprocess_diskread[process.name()] = process.io_counters()[2] /1024  #kb
                allprocess_diskwrite[process.name()] = process.io_counters()[3]/1024  #kb
                allprocess_networkusagesend[process.name()] = process.io_counters()[2]/1024
                allprocess_networkusagerecieve[process.name()] = process.io_counters()[3]/1024
            except Exception as exp:
                writer(str(exp))
            else:
                pass
        disksnstt = time.time()


        if len(backram_diskread) >= 2:
            del backram_diskread[0]
            backram_diskread.append(allprocess_diskread)
        else:
            backram_diskread.append(allprocess_diskread)

        if len(backram_diskwrite) >= 2:
            del backram_diskwrite[0]
            backram_diskwrite.append(allprocess_diskwrite)
        else:
            backram_diskwrite.append(allprocess_diskwrite)


        print("345", allprocess_networkusagesend)
        #print(allprocess_ram)
        kl = ls.shower(allprocess)
        kl1 = []
        proclen = 0
        for k in kl:
            m={}
            m[k] = kl[k]
            kl1.append(m)
            proclen += 1
            if proclen == 6:
                break

        kl1 = kl1[0:]
        print(kl1)

        for iif in kl1:
            for iz in iif.keys():
                print(iz)
                if iz == "System Idle Process":
                   pass
                else:
                    ife.influx_creator(org="H", bucket="SeconX", tablen="Custom_scripts", field_name="EX134proc", tag0=str(iz), tag1="34", value=int(iif[iz]), repeatedly=2, timezi=1)

        #print(allprocess_ram)
        kl = ls.shower(allprocess_ram)
        kl1 = []
        proclen = 0
        for k in kl:
            m={}
            m[k] = kl[k]
            kl1.append(m)
            proclen += 1
            if proclen == 6:
                break

        kl1 = kl1[0:]
        print(kl1, "ram")

        for iif in kl1:
            for iz in iif.keys():
                print(iz)
                if iz == "MemCompression":
                    pass
                else:
                    ife.influx_creator(org="H", bucket="SeconX", tablen="Custom_scripts", field_name="EX134procram", tag0=str(iz), tag1="34", value=int(iif[iz]), repeatedly=2, timezi=1)

        cpu_usage=psutil.cpu_percent(interval=1)
        print(cpu_usage)
        ife.influx_creator(org="H", bucket="SeconX", tablen="Custom_scripts", field_name="EX134cpu", tag0="cpu_usage", tag1="36", value=int(cpu_usage), repeatedly=2, timezi=1)
        # Getting % usage of virtual_memory ( 3rd field)
        print('RAM memory % used:', psutil.virtual_memory()[2])
        # Getting usage of virtual_memory in GB ( 4th field)
        print('RAM Used (GB):', psutil.virtual_memory()[3] / 1000000000)
        ife.influx_creator(org="H", bucket="SeconX", tablen="Custom_scripts", field_name="EX134pram", tag0="EX58ram", tag1="35", value=int(psutil.virtual_memory()[2]), repeatedly=2, timezi=1)
        #time.sleep(1)

        disksnend = time.time()
        diff = disksnend - disksnstt

        if len(backram_diskread) == 2:
            last = backram_diskread[0]
            new =  backram_diskread[1]
            updated_dict = {}
            for n in new.keys():
                if n in last.keys():    #geçmiş ve yeni değer sözlüklerde olduğuna emin olmalıyız
                    newval = new[n]
                    lastval = last[n]
                    diffvall = (newval-lastval) / 1024 #kbsec
                    diffvalls = diffvall / diff
                    updated_dict[n] = diffvalls

            print(updated_dict)
            reupdict = ls.shower(updated_dict)
            kl = reupdict
            kl1 = []
            proclen = 0
            for k in kl:
                m = {}
                m[k] = kl[k]
                kl1.append(m)
                proclen += 1
                if proclen == 6:
                    break

            print(kl1, "ram")

            for iif in kl1:
                for iz in iif.keys():
                    print(iz)
                    if iz == "MemCompression":
                        pass
                    else:
                        ife.influx_creator(org="H", bucket="SeconX", tablen="Custom_scripts", field_name="EX134diskr", tag0=str(iz), tag1="34", value=int(iif[iz]),repeatedly=2, timezi=1)


        if len(backram_diskwrite) == 2:
            last = backram_diskwrite[0]
            new =  backram_diskwrite[1]
            updated_dict = {}
            for n in new.keys():
                if n in last.keys():    #geçmiş ve yeni değer sözlüklerde olduğuna emin olmalıyız
                    newval = new[n]
                    lastval = last[n]
                    diffvall = (newval-lastval) / 1024 #kbsec
                    diffvalls = diffvall / diff
                    updated_dict[n] = diffvalls

            print(updated_dict)
            reupdict = ls.shower(updated_dict)
            print(reupdict)
            kl = reupdict
            kl1 = []
            proclen = 0
            for k in kl:
                m = {}
                m[k] = kl[k]
                kl1.append(m)
                proclen += 1
                if proclen == 6:
                    break


            for iif in kl1:
                for iz in iif.keys():
                    print(iz)
                    if iz == "MemCompression":
                        pass
                    else:
                        ife.influx_creator(org="H", bucket="SeconX", tablen="Custom_scripts",field_name="EX134diskw", tag0=str(iz), tag1="34", value=int(iif[iz]), repeatedly=2, timezi=1)
        end = time.time()
        restime = round(end-std, 2)
        print(restime, "sn")
        if restime < wait_time:
            print("waiting")
            time.sleep(wait_time-restime)
except Exception as eep:
    print(eep)
    writer(str(eep))
else:
    print("Başarılı")